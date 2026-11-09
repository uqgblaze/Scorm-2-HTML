"""Offline smoke tests: python -m unittest test_converter.py"""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from rise_scorm_to_html import convert, rich_text

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==')
COURSE = {
    'course': {
        'title': 'Mine Planning Fundamentals',
        'description': 'Example course only, containing synthetic learning material.',
        'lessons': [
            {'title':'Introduction to mine planning', 'type':'lesson', 'items':[
                {'family':'text','type':'text','variant':'heading paragraph','items':[{'heading':'Welcome','paragraph':'<p>A <strong>mine plan</strong> sequences extraction.</p>'}]},
                {'family':'media','type':'image','items':[{'image':{'src':'assets/diagram.png','alt':'Illustrative mine diagram'}, 'caption':'Mining illustration'}]},
                {'family':'interactive','type':'tabs','items':[{'title':'Open pit','description':'Surface extraction'},{'title':'Underground','description':'Subsurface extraction'}]},
                {'family':'interactive','type':'accordion','items':[{'title':'Ore grade','content':'Proportion of valuable material'},{'title':'Waste','content':'Non-ore material'}]},
                {'family':'knowledgeCheck','type':'multipleChoice','items':[{'question':'What is a cut-off grade?','choices':[{'text':'An economic threshold','correct':True},{'text':'A haul road length','correct':False}], 'feedback':'Used in deciding ore versus waste.'}]},
                {'family':'media','type':'video','items':[{'url':'https://example.edu/video.mp4'}]},
            ]},
            {'title':'Final quiz','type':'quiz','questions':[{'question':'Which is an ore characteristic?','options':[{'text':'Grade','correct':True},{'text':'Rainfall','correct':False}]}]},
        ]
    }
}

class TestConvert(unittest.TestCase):
    def write_zip(self, filename, mode):
        data = base64.b64encode(json.dumps(COURSE).encode('utf-8')).decode('ascii')
        with ZipFile(filename, 'w') as z:
            if mode == 'modern':
                z.writestr('scormcontent/runtime-data.js', f'__jsonp("runtime-data.js","{data}")')
            elif mode == 'old':
                z.writestr('scormcontent/index.html', f'<html><script>window.courseData = "{data}";</script></html>')
            elif mode == 'jsonp':
                z.writestr('scormcontent/assets/und.js', f'__resolveJsonp("course:und","{data}")')
            z.writestr('scormcontent/assets/diagram.png', PNG)

    def test_all_variants(self):
        for mode in ('modern','old','jsonp'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                path = Path(d) / 'test.zip'
                self.write_zip(path,mode)
                output, payload, stats = convert(path)
                result = output.read_text('utf-8')
                for expected in ('Welcome', 'Surface extraction', 'Subsurface extraction', 'Ore grade',
                                 'economic threshold', 'Correct answer', 'Final quiz', 'Mining illustration',
                                 'https://example.edu/video.mp4', 'data:image/png;base64,'):
                    self.assertIn(expected, result)
                self.assertEqual(stats['blocks'],7)
                self.assertEqual(stats['lessons'],2)
                self.assertTrue(payload.endswith('runtime-data.js' if mode=='modern' else 'index.html' if mode=='old' else 'und.js'))

    def test_sanitisation(self):
        bad = '<p>Hello<script>alert(1)</script><img src=x onerror=alert(2)><a href="javascript:evil()">bad</a></p>'
        safe = rich_text(bad)
        self.assertIn('Hello', safe)
        self.assertNotIn('alert',safe)
        self.assertNotIn('<img',safe)
        self.assertNotIn('javascript:',safe)

if __name__ == '__main__':
    unittest.main()
