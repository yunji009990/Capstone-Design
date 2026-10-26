"""실제 Dialogue 종료 계약으로 장기 기억 검사의 가짜 감지기 수명주기를 확인한다."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_dialogue_memory as tool


class MemoryEvaluationLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_fixture_can_close_a_real_dialogue(self):
        detector = tool.TextDetector()
        detector.audio.extend(b'input')
        detector.pending.extend(b'pending')
        if hasattr(detector, 'recent'):
            detector.recent.extend(b'recent')

        async def emit(event):
            pass

        dialogue = tool.Dialogue('검사용 인물', [], None, None, emit, detector=detector)
        dialogue.history.append({'role': 'user', 'content': '검사용 대화'})
        await dialogue.close()
        self.assertTrue(dialogue.closed)
        self.assertFalse(dialogue.history)
        self.assertFalse(detector.audio)
        self.assertFalse(detector.pending)
        self.assertFalse(detector.recent)


if __name__ == '__main__':
    unittest.main()
