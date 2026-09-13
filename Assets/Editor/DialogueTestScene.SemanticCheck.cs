using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

public static partial class DialogueTestScene
{
    [Serializable]
    class SemanticCase
    {
        public string expected, action, reason, transcript, answer, route, error;
        public string originalResponseId, finalResponseId, emotion;
        public string resumeAction, resumeTranscript;
        public bool passed, paused, pauseStable, sameResponseResumed, heldUntilRequested;
        public int cancellations;
        public long samplesAtPause, samplesAtResume, receivedAtPause, receivedAtResume;
        public float decisionSeconds, pauseAfterInputSeconds;
        public float resumeDecisionSeconds;
    }

    [Serializable]
    class SemanticReport
    {
        public bool passed;
        public string error, time;
        public List<SemanticCase> cases = new List<SemanticCase>();
    }

    // Requires mono PCM16 / 16 kHz ask, resume, revise, switch and hold WAVs in
    // .dialogue-work/semantic-fixtures. All inputs use the microphone transport.
    static async void RunSemanticCheck()
    {
        if (_checking) return;
        var panel = UnityEngine.Object.FindObjectOfType<DialogueTestPanel>();
        if (!EditorApplication.isPlaying || panel == null || panel.Voice.ExperienceActive)
        { Debug.LogWarning("Open the AI test scene in Play Mode with no active experience."); return; }
        _checking = true;
        string work = Path.GetFullPath(Path.Combine(Application.dataPath, "../.dialogue-work"));
        var report = new SemanticReport { time = DateTime.UtcNow.ToString("O") };
        var voice = panel.Voice;
        string persona = voice.testPersona;
        bool capture = voice.captureRealtimeMicrophone;
        SemanticCase current = null;
        string failure = null;
        Action<string> onError = message => failure = message;
        Action<string, string> onCancel = (heard, answer) => { if (current != null) current.cancellations++; };
        Action<string, string> onDecision = (action, text) =>
        {
            if (current == null) return;
            if (current.expected == "hold" && current.action == "hold")
            {
                current.resumeAction = action;
                current.resumeTranscript = text;
                current.resumeDecisionSeconds = voice.DecisionSeconds;
                return;
            }
            current.action = action;
            current.transcript = text;
            current.reason = voice.InterruptionReason;
            current.decisionSeconds = voice.DecisionSeconds;
        };
        voice.OnError += onError;
        voice.OnAnswerInterrupted += onCancel;
        voice.OnInterruptionDecision += onDecision;
        try
        {
            foreach (string name in new[] { "ask", "resume", "revise", "switch", "hold" })
                ReadSemanticPcm(Path.Combine(work, "semantic-fixtures", name + ".wav"));
            await Until(() => !panel.CheckingServer && voice.serverReady, 30, "Server was not ready.");
            if (!voice.TtsEnabled) throw new Exception("This playback check requires TTS. Use the text judge evaluation while TTS is off.");
            voice.testPersona = "너는 한국어로 말하는 음성 테스트 도우미다. 산책 준비를 설명해 달라고 하면 " +
                "준비물을 중심으로 구체적인 짧은 문장 네 개로 답한다. 다른 요청에는 그 요청에 답한다. " +
                "평범한 설명은 바로 답하고, 끝에 질문하지 않는다. 목록 번호와 마크다운을 쓰지 않는다.";
            foreach (string expected in new[] { "resume", "revise", "switch", "hold" })
            {
                current = new SemanticCase { expected = expected };
                report.cases.Add(current);
                if (!panel.BeginAudioFileCheck()) throw new Exception("Could not start audio test.");
                await Until(() => voice.DialogueConnected || failure != null, 90, "Connection timed out.");
                if (failure != null) throw new Exception(failure);
                await SendSemanticPcm(voice, Path.Combine(work, "semantic-fixtures/ask.wav"));
                await Until(() => voice.IsSpeaking || failure != null, 50, "Initial answer did not play.");
                if (failure != null) throw new Exception(failure);
                current.originalResponseId = voice.CurrentResponseId;
                float inputStart = Time.realtimeSinceStartup;
                float pausedAt = -1;
                long settledSamples = -1;
                Action observe = () =>
                {
                    if (!voice.ResponsePaused) return;
                    if (voice.IsSpeaking) throw new Exception("Playback continued while paused.");
                    if (pausedAt < 0)
                    {
                        pausedAt = Time.realtimeSinceStartup;
                        current.paused = true;
                        current.pauseAfterInputSeconds = pausedAt - inputStart;
                        current.receivedAtPause = voice.ReceivedAudioSamples;
                    }
                    // Allow the audio thread to apply Pause, then require its
                    // cursor to remain fixed while the user's WAV is streamed.
                    if (Time.realtimeSinceStartup - pausedAt < .2f) return;
                    if (settledSamples < 0)
                        current.samplesAtPause = settledSamples = voice.ConsumedAudioSamples;
                    else if (voice.ConsumedAudioSamples != settledSamples)
                        throw new Exception("Audio cursor advanced during pause.");
                    else current.pauseStable = true;
                };
                await SendSemanticPcm(voice, Path.Combine(work, "semantic-fixtures", expected + ".wav"), observe);
                await Until(() => current.action != null || failure != null, 15, "No interruption decision.", observe);
                if (failure != null) throw new Exception(failure);
                if (current.action != expected) throw new Exception("Expected " + expected + ", got " + current.action);
                if (!current.paused || !current.pauseStable) throw new Exception("Pause was not observed and stable.");
                current.emotion = voice.VoiceEmotion;
                if (expected == "hold")
                {
                    long position = voice.ConsumedAudioSamples;
                    await Task.Delay(1100);
                    current.heldUntilRequested = voice.ResponsePaused && !voice.IsSpeaking &&
                        voice.ConsumedAudioSamples == position && voice.CurrentResponseId == current.originalResponseId;
                    if (!current.heldUntilRequested) throw new Exception("Held answer restarted on its own.");
                    await SendSemanticPcm(voice, Path.Combine(work, "semantic-fixtures/resume.wav"));
                    await Until(() => current.resumeAction != null || failure != null, 15, "Held answer did not receive resume decision.");
                    if (current.resumeAction != "resume") throw new Exception("Explicit resume after hold was not accepted.");
                }
                if (expected == "resume" || expected == "hold")
                {
                    await Until(() => !voice.ResponsePaused || failure != null, 10, "Answer remained paused.");
                    current.finalResponseId = voice.CurrentResponseId;
                    current.samplesAtResume = voice.ConsumedAudioSamples;
                    current.receivedAtResume = voice.ReceivedAudioSamples;
                    current.sameResponseResumed = current.finalResponseId == current.originalResponseId;
                    if (!current.sameResponseResumed || current.cancellations != 0 ||
                        current.samplesAtResume < current.samplesAtPause || current.receivedAtResume < current.receivedAtPause)
                        throw new Exception("Resume replaced the answer or reset its audio buffer.");
                }
                else
                {
                    await Until(() => !string.IsNullOrEmpty(voice.CurrentResponseId) &&
                        voice.CurrentResponseId != current.originalResponseId || failure != null, 15, "New answer did not start.");
                    current.finalResponseId = voice.CurrentResponseId;
                    if (current.cancellations != 1) throw new Exception("Old answer was not cancelled exactly once.");
                }
                await Until(() => voice.PlaybackFinished || failure != null, 100, "Answer did not finish playback.");
                if (failure != null) throw new Exception(failure);
                current.answer = voice.lastAnswer;
                current.route = voice.ResponseRoute;
                if (string.IsNullOrEmpty(current.answer) || !panel.ConversationText.Contains(current.answer))
                    throw new Exception("Completed answer is missing from the conversation display.");
                current.passed = true;
                await Task.Delay(100);
                ScreenCapture.CaptureScreenshot(Path.Combine(work, "semantic-" + expected + ".png"));
                await Task.Delay(100);
                Debug.Log("[SemanticInterruptionCase] " + JsonUtility.ToJson(current));
                current = null;
                voice.EndExperience();
                await Task.Delay(500);
            }
            report.passed = true;
        }
        catch (Exception e)
        {
            report.error = e.ToString();
            if (current != null) current.error = e.Message;
        }
        finally
        {
            if (voice != null)
            {
                voice.OnError -= onError;
                voice.OnAnswerInterrupted -= onCancel;
                voice.OnInterruptionDecision -= onDecision;
                voice.EndExperience();
                voice.testPersona = persona;
                voice.captureRealtimeMicrophone = capture;
            }
            Directory.CreateDirectory(work);
            File.WriteAllText(Path.Combine(work, "semantic-interruption-check.json"), JsonUtility.ToJson(report, true));
            Debug.Log("[SemanticInterruptionCheck] " + JsonUtility.ToJson(report));
            _checking = false;
        }
    }

    static async Task Until(Func<bool> predicate, int seconds, string error, Action observe = null)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (!predicate())
        {
            if (!EditorApplication.isPlaying) throw new Exception("Play Mode ended during the test.");
            if (DateTime.UtcNow >= deadline) throw new Exception(error);
            observe?.Invoke();
            await Task.Delay(20);
        }
    }

    static async Task SendSemanticPcm(DialogueVoiceClient voice, string path, Action observe = null)
    {
        byte[] pcm = ReadSemanticPcm(path);
        for (int offset = 0; offset < pcm.Length + 32000; offset += 2560)
        {
            var packet = new byte[2560];
            if (offset < pcm.Length) Buffer.BlockCopy(pcm, offset, packet, 0, Math.Min(2560, pcm.Length - offset));
            if (!voice.SendTestAudio(packet)) throw new Exception("Audio packet was not sent.");
            await Task.Delay(80);
            observe?.Invoke();
        }
    }

    static byte[] ReadSemanticPcm(string path)
    {
        byte[] wav = File.ReadAllBytes(path);
        bool format = false;
        for (int offset = 12; offset + 8 <= wav.Length;)
        {
            string kind = System.Text.Encoding.ASCII.GetString(wav, offset, 4);
            int size = BitConverter.ToInt32(wav, offset + 4);
            offset += 8;
            if (size < 0 || size > wav.Length - offset) break;
            if (kind == "fmt " && size >= 16)
                format = BitConverter.ToInt16(wav, offset) == 1 && BitConverter.ToInt16(wav, offset + 2) == 1 &&
                    BitConverter.ToInt32(wav, offset + 4) == 16000 && BitConverter.ToInt16(wav, offset + 14) == 16;
            if (kind == "data" && format && size > 0 && size % 2 == 0)
            {
                var pcm = new byte[size];
                Buffer.BlockCopy(wav, offset, pcm, 0, size);
                return pcm;
            }
            offset += size + (size & 1);
        }
        throw new Exception("Expected mono PCM16 / 16 kHz WAV: " + Path.GetFileName(path));
    }
}
