import sys
import os

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.language import detect_language, Language
from app.services.diff_parser import parse_file_diff, _detect_language
from app.tools.registry import get_analyser, registered_languages

def test():
    assert detect_language("test.py") == Language.PYTHON
    assert detect_language("test.js") == Language.JAVASCRIPT
    assert detect_language("test.tsx") == Language.TYPESCRIPT
    assert detect_language("test.mjs") == Language.JAVASCRIPT
    
    assert _detect_language("test.py") == Language.PYTHON

    analyser = get_analyser(Language.PYTHON)
    assert analyser is not None
    
    langs = registered_languages()
    assert Language.PYTHON in langs
    assert Language.JAVASCRIPT in langs

    print("All tests passed!")

if __name__ == "__main__":
    test()

