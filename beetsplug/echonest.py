import time
import logging
import socket

from beets import util, config, plugins, ui, library
import pyechonest
import pyechonest.song
import pyechonest.track

log = logging.getLogger('beets')

RETRY_INTERVAL = 10 # seconds
RETRIES = 10

def mapper(field, mapping, min_v=0.0, max_v=1.0):
    def fieldfunc(item):
        try:
            value = item.get('echonest_{}'.format(field), None)
            if value is None:
                return None
            value = float(value)
            return mapping[min(
                    len(mapping) - 1,
                    int((value - min_v) / ((max_v - min_v) / len(mapping)))
                )]
        except ValueError, TypeError:
            return None
    return fieldfunc

def _splitstrip(string):
    """Split string at comma and return the stripped values as array."""
    return [ s.strip() for s in string.split(u',') ]

class MappingQuery(library.FieldQuery):
    def __init__(self, field, pattern, fast=True):
        print(field, pattern, fast)
        super(MappingQuery, self).__init__(field, pattern, fast)
        self.mapping = _splitstrip(pattern)

    def match(self, item):
        return item.get(self.field) in self.mapping

class GTQuery(library.FieldQuery):
    def match(self, item):
        try:
            return (float(item.get('echonest_{}'.format(self.field))) >
            float(self.pattern))
        except TypeError:
            return False

class LTQuery(library.FieldQuery):
    def match(self, item):
        try:
            return (float(item.get('echonest_{}'.format(self.field))) <
            float(self.pattern))
        except TypeError:
            return False


class EchonestMetadataPlugin(plugins.BeetsPlugin):
    _songs = {}
    _attributes = []
    _no_mapping = []

    def __init__(self):
        super(EchonestMetadataPlugin, self).__init__()
        self.config.add({
                'auto': True,
                'apikey': u'NY2KTZHQ0QDSHBAP6',
                'codegen': None,
                'attributes': u'energy,liveness,speechiness,acousticness,' \
                               'danceability,valence,tempo',
                'no_mapping': u'tempo',
                'mapping': u'very low,low,neutral,high,very high',
                'speechiness_mapping': u'singing,probably singing,' \
                                        'probably talking,talking',
                'danceability_mapping': u'bed,couch,party,disco',
            })

        pyechonest.config.ECHO_NEST_API_KEY = \
            config['echonest']['apikey'].get(unicode)
        if config['echonest']['codegen'].get() is not None:
            pyechonest.config.CODEGEN_BINARY_OVERRIDE = \
                config['echonest']['codegen'].get(unicode)

        self._attributes = _splitstrip(
                config['echonest']['attributes'].get(unicode))
        self._no_mapping = _splitstrip(
                config['echonest']['no_mapping'].get(unicode))
        self.register_listener('import_task_start', self.fetch_song_task)
        self.register_listener('import_task_apply', self.apply_metadata_task)

        global_mapping = _splitstrip(
                config['echonest']['mapping'].get(unicode))

        for attr in self._attributes:
            if attr in self._no_mapping:
                continue
            mapping = global_mapping
            key = '{}_mapping'.format(attr)
            self.config.add({key:None})
            if config['echonest'][key].get() is not None:
                mapping = _splitstrip(
                        config['echonest'][key].get(unicode))
            self.template_fields[attr] = mapper(attr, mapping)

    def queries(self):
        return {
                '[' : MappingQuery,
                '<' : LTQuery,
                '>' : GTQuery,
        }

    def _echofun(self, func, **kwargs):
        for i in range(RETRIES):
            try:
                result = func(**kwargs)
            except pyechonest.util.EchoNestAPIError as e:
                if e.code == 3:
                    # reached access limit per minute
                    # log.debug(u'echonest: {}'.format(e))
                    time.sleep(RETRY_INTERVAL)
                elif e.code == 5:
                    # specified identifier does not exist
                    # log.debug(u'echonest: {}'.format(e))
                    return None
                else:
                    log.error(u'echonest: {0}'.format(e.args[0][0]))
                    return None
            except (pyechonest.util.EchoNestIOError, socket.error) as e:
                log.warn(u'echonest: IO error: {0}'.format(e))
                time.sleep(RETRY_INTERVAL)
            else:
                break
        else:
            # If we exited the loop without breaking, then we used up all
            # our allotted retries.
            raise Exception(u'exceeded retries')
            return None
        return result

    def fingerprint(self, item):
        if item.get('echonest_fingerprint', None) is None:
            try:
                code = self._echofun(pyechonest.util.codegen, filename=item.path)
                item['echonest_fingerprint'] = code[0]['code']
                item.write()
            except Exception as exc:
                log.error(u'echonest: fingerprinting failed: {0}'
                        .format(str(exc)))
                return None
        return item.get('echonest_fingerprint')

    def analyze(self, item):
        try:
            track = self._echofun(pyechonest.track.track_from_filename,
                    filename=item.path)
            if track is None:
                raise Exception(u'failed to upload file')
            songs = self._echofun(pyechonest.song.profile,
                    ids=[track.song_id], track_ids=[track.id],
                    buckets=['audio_summary'])
            if songs is None:
                raise Exception(u'failed to retrieve info from upload')
            return songs[0]
        except Exception as exc:
            log.error(u'echonest: analysis failed: {0}'.format(str(exc)))

    def identify(self, item):
        try:
            code = self.fingerprint(item)
            if code is None:
                raise Exception(u'can not identify without a fingerprint')
            songs = self._echofun(pyechonest.song.identify, code=code)
            if not songs:
                raise Exception(u'no songs found')
            return max(songs, key=lambda s: s.score)
        except Exception as exc:
            log.error(u'echonest: identification failed: {0}'.format(str(exc)))

    def _pick_song(self, songs, item):
        pick = None
        if songs:
            min_dist = item.length
            for song in songs:
                if song.artist_name.lower() == item.artist.lower() \
                        and song.title.lower() == item.title.lower():
                    dist = abs(item.length - song.audio_summary['duration'])
                    if dist < min_dist:
                        min_dist = dist
                        pick = song
            if min_dist > 1.0:
                return None
        return pick

    def search(self, item):
        try:
            songs = self._echofun(pyechonest.song.search, title=item.title,
                    results=100, artist=item.artist,
                    buckets=['id:musicbrainz', 'tracks'])
            pick = self._pick_song(songs, item)
            if pick is None:
                raise Exception(u'no (matching) songs found')
            return pick
        except Exception as exc:
            log.error(u'echonest: search failed: {0}'.format(str(exc)))
            return None

    def profile(self, item):
        try:
            if item.get('echonest_id', None) is None:
                if not item.mb_trackid:
                    raise Exception(u'musicbrainz ID not available')
                mbid = 'musicbrainz:track:{0}'.format(item.mb_trackid)
                track = self._echofun(pyechonest.track.track_from_id, identifier=mbid)
                if not track:
                    raise Exception(u'could not get track from ID')
                ids = track.song_id
            else:
                ids = item.get('echonest_id')
            songs = self._echofun(pyechonest.song.profile, ids=ids,
                    buckets=['id:musicbrainz', 'audio_summary'])
            if not songs:
                raise Exception(u'could not get songs from track ID')
            # FIXME: can we trust this or should we double check duration?  We
            # can't trust it :( durations can differ significantly
            return self._pick_song(songs, item)
        except Exception as exc:
            log.debug(u'echonest: profile failed: {0}'.format(str(exc)))
            return None

    def fetch_song(self, item):
        for method in [self.profile, self.search, self.identify, self.analyze]:
            try:
                song = method(item)
                if not song is None:
                    log.debug(u'echonest: got song through {0}: {1} - {2} [{3}]'
                              .format(method.im_func.func_name,
                              song.artist_name, song.title,
                              song.audio_summary['duration']))
                    return song
            except Exception as exc:
                log.debug(u'echonest: profile failed: {0}'.format(str(exc)))
        return None

    def apply_metadata(self, item):
        if item.path in self._songs:
            item.echonest_id = self._songs[item.path].id
            for k, v in self._songs[item.path].audio_summary.iteritems():
                if k in self._attributes:
                    log.debug(u'echonest: metadata: {0} = {1}'.format(k, v))
                    item['echonest_{}'.format(k)] = v
            if config['import']['write'].get(bool):
                log.info(u'echonest: writing metadata: {0}'
                         .format(util.displayable_path(item.path)))
                item.write()
                if item._lib:
                    item.store()
        else:
            log.warn(u'echonest: no metadata available')

    def fetch_song_task(self, task, session):
        items = task.items if task.is_album else [task.item]
        for item in items:
            song = self.fetch_song(item)
            if not song is None:
                self._songs[item.path] = song

    def apply_metadata_task(self, task, session):
        for item in task.imported_items():
            self.apply_metadata(item)

    def commands(self):
        cmd = ui.Subcommand('echonest',
            help='Fetch metadata from the EchoNest')

        def func(lib, opts, args):
            for item in lib.items(ui.decargs(args)):
                log.info(u'echonest: {0} - {1} [{2}]'.format(item.artist,
                        item.title, item.length))
                song = self.fetch_song(item)
                if not song is None:
                    self._songs[item.path] = song
                self.apply_metadata(item)

        cmd.func = func
        return [cmd]

# eof
