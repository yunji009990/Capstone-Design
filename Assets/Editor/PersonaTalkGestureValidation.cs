using System;
using System.IO;
using UnityEditor;
using UnityEngine;

// 시간·음량과 무관한 선택 정책 검사. 실제 재생/음성 연결은 별도 Play 검사로 확인한다.
public static class PersonaTalkGestureValidation
{
    const string Folder = "tools/_work/sitting_talk_20260930";
    [Serializable] class Report
    {
        public string utc, error;
        public bool success, noConsecutive, sameResponseOnce, resumeOnce, reset, zeroRate, maximumRate;
        public int responses, selected;
        public float observedRate;
    }

    [MenuItem("Tools/Persona/대화 제스처 선택 검사", priority = 25)]
    public static void Run()
    {
        var r = new Report { utc = DateTime.UtcNow.ToString("o"), responses = 100000 };
        try
        {
            var gate = new PersonaTalkGestureSelector();
            var random = new System.Random(20260930);
            bool previous = false;
            for (int i = 0; i < r.responses; i++)
            {
                string id = "qa_" + i;
                bool selected = gate.TrySelect(id, .25f, (float)random.NextDouble());
                Require(!(previous && selected), "연속 선택");
                Require(!gate.TrySelect(id, .25f, 0), "같은 답변을 다시 선택");
                if (selected) r.selected++;
                previous = selected;
            }
            r.observedRate = r.selected / (float)r.responses;
            Require(Mathf.Abs(r.observedRate - .25f) < .005f, "전체 평균이 25%에서 벗어남");
            r.noConsecutive = r.sameResponseOnce = true;
            gate.Reset();
            Require(gate.TrySelect("A", .25f, 0), "첫 응답 선택 실패");
            Require(!gate.TrySelect("", .25f, 0), "빈 응답 선택");
            Require(!gate.TrySelect("A", .25f, 0), "음성 재개를 새 발화로 셈");
            Require(!gate.TrySelect("B", .25f, 0), "다음 답변을 건너뛰지 않음");
            Require(!gate.TrySelect("A", .25f, 0), "지나간 답변 재선택");
            Require(gate.TrySelect("C", .25f, 0), "중복 요청이 다음 선택에 영향");
            r.resumeOnce = true;
            gate.Reset();
            Require(gate.TrySelect("A", .25f, 0), "새 체험에서 선택 상태 초기화 실패");
            r.reset = true;
            gate.Reset();
            for (int i = 0; i < 20; i++) Require(!gate.TrySelect(i.ToString(), 0, 0), "0%에서 선택");
            r.zeroRate = true;
            gate.Reset();
            for (int i = 0; i < 20; i++)
                Require(gate.TrySelect(i.ToString(), .5f, 0) == (i % 2 == 0), "50% 경계에서 연속 방지 실패");
            r.maximumRate = true;
            r.success = true;
        }
        catch (Exception e) { r.error = e.Message; }
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Folder + "/selection_validation.json", JsonUtility.ToJson(r, true));
        Debug.Log($"[TalkGestureValidation] success={r.success}, rate={r.observedRate:P2}; {r.error}");
    }
    static void Require(bool value, string error) { if (!value) throw new InvalidOperationException(error); }
}
