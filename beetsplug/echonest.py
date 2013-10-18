import time
import logging
import socket

from beets import util, config, plugins, ui
import pyechonest
import pyechonest.song
import pyechonest.track

log = logging.getLogger('beets')

RETRY_INTERVAL = 10  # seconds
RETRIES = 10

def mapper(field, mapping, min_v=0.0, max_v=1.0):
    def fieldfunc(item):
        try:
            value = item.get(field, None)
            if value is None:
                return u'not set'
            value = float(value)
            inc = (max_v - min_v) / len(mapping)
            i = min_v
            for m in mapping:
                i += inc
                if value < i:
                    return m
            return m # in case of floating point precision problems
        except ValueError:
            return item.get(field)
    return fieldfunc

class EchonestMetadataPlugin(plugins.BeetsPlugin):
    _songs = {}

    def __init__(self):
        super(EchonestMetadataPlugin, self).__init__()
        self.config.add({'auto': True, 'apikey': u'', 'codegen': u''})
        pyechonest.config.ECHO_NEST_API_KEY = \
            config['echonest']['apikey'].get(unicode)
        if config['echonest']['codegen'].get(unicode) != u'':
            pyechonest.config.CODEGEN_BINARY_OVERRIDE = \
                config['echonest']['codegen'].get(unicode)
        self.register_listener('import_task_start', self.fetch_song_task)
        self.register_listener('import_task_apply', self.apply_metadata_task)

        self.template_fields['speechiness'] = mapper('speechiness',
                ['singing', 'rapping', 'speaking'])
        self.template_fields['danceability'] = mapper('danceability',
                ['bed', 'couch', 'party', 'disco'])

    def _echofun(self, func, **kwargs):
        for i in range(RETRIES):
            try:
                result = func(**kwargs)
            except pyechonest.util.EchoNestAPIError as e:
                if e.code == 3:
                    # reached access limit per minute
                    time.sleep(RETRY_INTERVAL)
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
                log.error('echonest: fingerprinting failed: {0}: {1}'
                          .format(item.path, str(exc)))
        log.debug('echonest: fingerprinted {0}'.format(item.path))
        return item.echonest_fingerprint

    def analyze(self, item):
        try:
            track = self._echofun(pyechonest.track.track_from_filename,
                    filename=item.path)
            self._echofun(pyechonest.song.profile, track_ids=[track.id])
        except Exception as exc:
            log.error('echonest: analysis failed: {0}: {1}'
                      .format(util.syspath(item.path), str(exc)))

    def identify(self, item):
        try:
            songs = self._echofun(pyechonest.song.identify, code=self.fingerprint(item))
            if not songs:
                raise Exception(u'no songs found')
            return max(songs, key=lambda s: s.score)
        except Exception as exc:
            log.error('echonest: identification failed: {0}: {1}'
                      .format(util.syspath(item.path), str(exc)))

    def search(self, item):
        try:
            songs = self._echofun(pyechonest.song.search, title=item.title,
                    artist=item.artist, buckets=['id:musicbrainz', 'tracks'])
            pick = None
            if songs:
                min_dist = item.length
                for song in songs:
                    if song.artist.name.lower() == item.artist.lower() \
                            and song.title.lower() == item.title.lower():
                        dist = abs(item.length - song.audio_summary['duration'])
                        if dist < min_dist:
                            min_dist = dist
                            pick = song
            if pick is None:
                raise Exception(u'no songs found')
            log.info(u'echonest: candidate distance {0}'.format(min_dist))
            return pick
        except Exception as exc:
            log.error('echonest: search failed: {0}: {1}'
                      .format(util.syspath(item.path), str(exc)))

    def profile(self, item):
        try:
            if not item.mb_trackid:
                raise Exception(u'musicbrainz ID not available')
            mbid = 'musicbrainz:track:{0}'.format(item.mb_trackid)
            track = self._echofun(pyechonest.track.track_from_id, identifier=mbid)
            songs = self._echofun(pyechonest.song.profile, ids=track.song_id,
                    buckets=['id:musicbrainz', 'audio_summary'])
            # FIXME: can we trust this or should we double check duration?
            return songs[0]
        except Exception as exc:
            log.error('echonest: profile failed: {0}: {1}'
                      .format(util.syspath(item.path), str(exc)))

    def fetch_song(self, item):
        for method in [self.profile, self.search, self.identify, self.analyze]:
            try:
                song = method(item)
                if song:
                    log.debug('echonest: got song through {0}: {1} - {2} [{3}]'
                              .format(method.im_func.func_name,
                              song.artist_name, song.title,
                              song.audio_summary['duration']))
                    return song
            except Exception as exc:
                log.error('echonest: tagging: {0}: {1}'
                          .format(util.syspath(item.path), str(exc)))

    def apply_metadata(self, item):
        if item.path in self._songs:
            item.echonest_id = self._songs[item.path].id
            for k, v in self._songs[item.path].audio_summary.iteritems():
                log.info(u'echonest: metadata: {0} - {1}'.format(k, v))
                setattr(item, k, v)
            if config['import']['write'].get(bool):
                log.info(u'echonest: writing metadata: {0}'
                         .format(util.displayable_path(item.path)))
                item.write()
                if item._lib:
                    item.store()
        else:
            log.warn(u'echonest: no metadata available: {0}'.
                     format(util.displayable_path(item.path)))

    def fetch_song_task(self, task, session):
        items = task.items if task.is_album else [task.item]
        for item in items:
            self._songs[item.path] = self.fetch_song(item)

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
              self._songs[item.path] = self.fetch_song(item)
              self.apply_metadata(item)

        cmd.func = func
        return [cmd]

# eof
