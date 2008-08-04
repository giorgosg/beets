#!/usr/bin/env python

"""
Test the MediaFile metadata layer.
"""

import unittest, sys, os, shutil
sys.path.append('..')
import beets.mediafile


def MakeReadingTest(path, correct_dict, field):
    class ReadingTest(unittest.TestCase):
        def setUp(self):
            self.f = beets.mediafile.MediaFile(path)
        def runTest(self):
            got = getattr(self.f, field)
            correct = correct_dict[field]
            self.assertEqual(got, correct,
                field + ' incorrect (expected ' + repr(correct) + ', got ' + \
                repr(got) + ') when testing ' + os.path.basename(path))
    return ReadingTest

def MakeWritingTest(path, correct_dict, field, testsuffix='_test'):
    
    class WritingTest(unittest.TestCase):
        def setUp(self):
            # make a copy of the file we'll work on
            root, ext = os.path.splitext(path)
            self.tpath = root + testsuffix + ext
            shutil.copy(path, self.tpath)
            
            # generate the new value we'll try storing
            if type(correct_dict[field]) is unicode:
                self.value = u'TestValue: ' + field
            elif type(correct_dict[field]) is int:
                self.value = correct_dict[field] + 42
            elif type(correct_dict[field]) is bool:
                self.value = not correct_dict[field]
            else:
                raise ValueError('unknown field type ' + \
                        str(type(correct_dict[field])))
        
        def runTest(self):    
            # write new tag
            a = beets.mediafile.MediaFile(self.tpath)
            setattr(a, field, self.value)
            a.save()
            
            # verify ALL tags are correct with modification
            b = beets.mediafile.MediaFile(self.tpath)
            for readfield in correct_dict.keys():
                got = getattr(b, readfield)
                if readfield is field:
                    self.assertEqual(got, self.value,
                        field + ' modified incorrectly (changed to ' + \
                        repr(self.value) + ' but read ' + repr(got) + \
                        ') when testing ' + os.path.basename(path))
                else:
                    correct = getattr(a, readfield)
                    self.assertEqual(got, correct,
                        readfield + ' changed when it should not have (expected'
                        ' ' + repr(correct) + ', got ' + repr(got) + ') when '
                        'modifying ' + field + ' in ' + os.path.basename(path))
                
        def tearDown(self):
            os.remove(self.tpath)
    
    return WritingTest

correct_dicts = {

    'full': {
        'title':      u'full',
        'artist':     u'the artist',
        'album':      u'the album',
        'genre':      u'the genre',
        'composer':   u'the composer',
        'grouping':   u'the grouping',
        'year':       2001,
        'month':      0,
        'day':        0,
        'track':      2,
        'tracktotal': 3,
        'disc':       4,
        'disctotal':  5,
        'lyrics':     u'the lyrics',
        'comments':   u'the comments',
        'bpm':        6,
        'comp':       True
    },

    'partial': {
        'title':      u'partial',
        'artist':     u'the artist',
        'album':      u'the album',
        'genre':      u'',
        'composer':   u'',
        'grouping':   u'',
        'year':       0,
        'month':      0,
        'day':        0,
        'track':      2,
        'tracktotal': 0,
        'disc':       4,
        'disctotal':  0,
        'lyrics':     u'',
        'comments':   u'',
        'bpm':        0,
        'comp':       False
    },

    'min': {
        'title':      u'min',
        'artist':     u'',
        'album':      u'',
        'genre':      u'',
        'composer':   u'',
        'grouping':   u'',
        'year':       0,
        'month':      0,
        'day':        0,
        'track':      0,
        'tracktotal': 0,
        'disc':       0,
        'disctotal':  0,
        'lyrics':     u'',
        'comments':   u'',
        'bpm':        0,
        'comp':       False
    },
    
    # empty.mp3 has had its ID3 tag deleted with mp3info -d
    'empty': {
        'title':      u'',
        'artist':     u'',
        'album':      u'',
        'genre':      u'',
        'composer':   u'',
        'grouping':   u'',
        'year':       0,
        'month':      0,
        'day':        0,
        'track':      0,
        'tracktotal': 0,
        'disc':       0,
        'disctotal':  0,
        'lyrics':     u'',
        'comments':   u'',
        'bpm':        0,
        'comp':       False
    },
    
    # full release date
    'date': {
        'year':       1987,
        'month':      3,
        'day':        31
    },

}

def suite_for_file(path, correct_dict):
    s = unittest.TestSuite()
    for field in correct_dict:
        s.addTest(MakeReadingTest(path, correct_dict, field)())
        if not (   field == 'month' and correct_dict['year']  == 0
                or field == 'day'   and correct_dict['month'] == 0):
             # ensure that we don't test fields that can't be modified
             s.addTest(MakeWritingTest(path, correct_dict, field)())
    return s

def suite():
    s = unittest.TestSuite()
    
    # General tests.
    for kind in ('m4a', 'mp3', 'flac'):
        for tagset in ('full', 'partial', 'min'):
            path = os.path.join('rsrc', tagset + '.' + kind)
            correct_dict = correct_dicts[tagset]
            s.addTest(suite_for_file(path, correct_dict))
    
    # Special test for missing ID3 tag.
    s.addTest(suite_for_file(os.path.join('rsrc', 'empty.mp3'),
                             correct_dicts['empty']))
    
    # Special test for advanced release date.
    s.addTest(suite_for_file(os.path.join('rsrc', 'date.mp3'),
                             correct_dicts['date']))
    
    return s

if __name__ == '__main__':
    unittest.main(defaultTest='suite')