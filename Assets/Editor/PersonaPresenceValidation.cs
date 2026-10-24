using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Rendering.Universal;

// 합성 상태·PCM만 쓴다. 운영 세션·참조 음성·마이크·유료 API는 사용하지 않는다.
public static class PersonaPresenceValidation
{
    const string Folder = "tools/_work/presence_20261023";
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static bool capturing;

    [Serializable] class Report
    {
        public string utc = DateTime.UtcNow.ToString("o"), error, spatializer;
        public bool success;
        public bool listenerPaused, editorMuted;
        public float listenerVolume;
        public List<string> passed = new List<string>();
        public List<Output> output = new List<Output>();
        public string limits;
    }
    [Serializable] class Output
    {
        public string name;
        public double left, right;
        public long consumed, callbacks;
        public int outputRate;
        public bool sourcePlaying, rendererEnabled;
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/듣기 반응·공간 음향")]
    public static void Run()
    {
        var r = new Report { limits = "합성 입력·직접 메서드 검사. 실제 HMD 청취·서버 대화·프레임 성능은 별도다." };
        r.listenerPaused = AudioListener.pause;
        r.listenerVolume = AudioListener.volume;
        r.editorMuted = EditorUtility.audioMasterMute;
        var random = UnityEngine.Random.state;
        try
        {
            foreach (int fps in new[] { 30, 60, 144 }) CheckNods(r, fps);
            using (var f = new Fixture())
            {
                f.audio.volume = .73f;
                f.audio.spatialBlend = 1f;
                f.audio.spatialize = f.audio.spatializePostEffects = true;
                var renderer = f.source.GetComponent<DialogueAudioRenderer>();
                if (renderer == null) renderer = f.source.AddComponent<DialogueAudioRenderer>();
                using (var player = new DialogueAudioPlayer(f.audio))
                    Require(f.audio.spatialBlend == 1f && f.audio.spatializePostEffects,
                        "스트리밍 생성자가 공간 음향 값을 덮어씀");
                UnityEngine.Object.DestroyImmediate(renderer);
                r.passed.Add("스트리밍 생성 시 3D 설정 보존");

                f.audio.spatialBlend = 0f;
                f.audio.spatialize = f.audio.spatializePostEffects = false;
                var oldPosition = f.audio.transform.position;
                f.spatial.RefreshSource();
                Require(f.spatial.FollowingHead && f.audio.spatialBlend == 1f &&
                    f.audio.spatialize && f.audio.spatializePostEffects && f.audio.dopplerLevel == 0f,
                    "머리 음원 또는 PCM 뒤 공간화 설정 실패");
                Require(Mathf.Abs(f.audio.volume - .73f) < .0001f, "사용자 음량을 변경함");
                r.passed.Add("머리 연결·공간화 순서·도플러 제거·음량 보존");
                f.head.position += new Vector3(.3f, .1f, -.2f);
                f.spatial.RefreshSource();
                Require(Vector3.Distance(f.audio.transform.position,
                    f.head.position + f.body.transform.rotation * f.spatial.headOffsetMeters) < .0001f,
                    "머리 이동 추종 실패");
                r.passed.Add("머리 이동 추종");
                f.body.SetActive(false);
                f.spatial.RefreshSource();
                Require(!f.spatial.FollowingHead && f.audio.spatialBlend == 0f, "음성 전용 2D 복귀 실패");
                f.body.SetActive(true);
                f.spatial.spatialVoice = false;
                f.spatial.RefreshSource();
                Require(f.audio.spatialBlend == 0f, "Inspector 공간 음향 끄기 실패");
                r.passed.Add("모델 숨김·공간 음향 끄기 시 2D 복귀");
                f.RestoreAudio();
                Require(f.audio.spatialBlend == 0f && !f.audio.spatialize && !f.audio.spatializePostEffects &&
                    Vector3.Distance(f.audio.transform.position, oldPosition) < .0001f,
                    "비활성 시 기존 음원 설정 복원 실패");
                r.passed.Add("비활성 시 원래 위치·설정 복원");
                CheckConfirmedTurn(f, r);
            }

            if (!EditorApplication.isPlaying) CheckRig(r);

            if (SceneManager.GetActiveScene().path == "Assets/Scenes/Scene_2.unity")
            {
                var s = UnityEngine.Object.FindObjectOfType<PersonaSpawner>();
                Require(s != null && s.arrival != null && s.arrival.autoNodWhileListening &&
                    s.GetComponent<PersonaVoiceSpatialAudio>() != null, "Scene_2 기능 연결 누락");
                r.passed.Add("Scene_2 듣기 반응·공간 음향 연결");
            }
            r.spatializer = AudioSettings.GetSpatializerPluginName();
            Require(r.spatializer == "Meta XR Audio", "기존 Meta XR Audio 설정 확인 필요");
            r.success = true;
        }
        catch (Exception e) { r.error = e.ToString(); }
        finally { UnityEngine.Random.state = random; Write("validation.json", r); }
    }

    static void CheckNods(Report r, int fps)
    {
        var n = new PersonaListeningNod();
        float dt = 1f / fps;
        Func<bool, bool, float> tick = (listening, blocked) => n.Tick(dt, listening, blocked, 1.2f, 4.5f, 4.5f, .9f);
        for (int i = 0; i < fps * 10; i++) tick(false, false);
        Require(n.Count == 0 && n.Weight == 0f, "무음에 반응함");
        for (int i = 0; i < fps / 2; i++) tick(true, false);
        for (int i = 0; i < fps; i++) tick(false, false);
        for (int i = 0; i < fps; i++) tick(true, false);
        Require(n.Count == 0, "짧은 발화를 무관한 다음 발화와 합침");
        n.Reset();
        float max = 0f;
        for (int i = 0; i < fps * 3; i++) max = Mathf.Max(max, tick(true, false));
        Require(n.Count == 1 && max > .98f && n.Weight == 0f, "끄덕임 한 번의 진입·복귀 실패");
        for (int i = 0; i < fps; i++) tick(true, false);
        Require(n.Count == 1, "간격 전에 다시 끄덕임");
        for (int i = 0; i < fps * 10; i++) tick(true, true);
        Require(n.Count == 1 && n.Weight == 0f, "인물 말하기·다른 제스처 중 새 끄덕임");
        n.Reset();
        for (int i = 0; i < Mathf.CeilToInt(fps * 1.65f); i++) tick(true, false);
        float before = n.Weight;
        float after = tick(true, true);
        Require(before > .9f && after > 0f && after < before, "중단 시 머리가 순간 이동함");
        for (int i = 0; i < fps; i++) tick(false, true);
        Require(n.Weight == 0f, "중단 후 기울기 잔류");
        n.Reset();
        Require(n.Count == 0 && n.Weight == 0f && !n.StartedThisFrame, "새 체험 초기화 실패");
        r.passed.Add(fps + "fps: 무음·짧은 발화·한 번 재생·간격·발화 중 차단·부드러운 복귀·초기화");
    }

    static void CheckConfirmedTurn(Fixture f, Report r)
    {
        var voice = f.source.GetComponent<DialogueVoiceClient>();
        var arrival = f.owner.AddComponent<PersonaArrival>();
        arrival.enabled = false;
        arrival.phase = PersonaArrival.Phase.앉음;
        arrival.autoNodWhileListening = true;
        Set(arrival, "_spawner", f.spawner);
        Set(voice, "_experienceActive", true);
        Set(voice, "_dialogueReady", true);
        voice.showHeardSubtitle = false;
        var eventType = typeof(DialogueVoiceClient).GetNestedType("DialogueEvent", BindingFlags.NonPublic);
        var receive = typeof(DialogueVoiceClient).GetMethod("HandleDialogueEvent", Private);
        foreach (var json in new[] {
            "{\"type\":\"speech.started\",\"turn_id\":1}",
            "{\"type\":\"speech.stopped\",\"turn_id\":1}",
            "{\"type\":\"transcript.final\",\"turn_id\":1,\"text\":\"synthetic statement\",\"audio\":{\"audio_event\":\"speech\",\"prosody\":{\"duration_sec\":2}}}"
        }) receive.Invoke(voice, new[] { JsonUtility.FromJson(json, eventType) });
        Require(!voice.isRecording && voice.isWaiting, "실제 서버와 같은 연속 신호 조건이 아님");
        var drive = typeof(PersonaArrival).GetMethod("DriveNodGesture", Private);
        drive.Invoke(arrival, null);
        Require(arrival.ListeningNodCount == 1, "시작·종료가 같은 프레임인 확정 발화에 반응하지 않음");
        drive.Invoke(arrival, null);
        Require(arrival.ListeningNodCount == 1, "동일 확정 전사에 중복 반응함");
        arrival.Stop();
        Require(arrival.ListeningNodCount == 0, "입장 연출 종료 후 반응 상태 잔류");
        r.passed.Add("실제 started→stopped→final 신호: 자막을 꺼도 한 번 반응·종료 초기화");
        Set(voice, "_experienceActive", false);
        Set(voice, "_dialogueReady", false);
    }

    static void CheckRig(Report r)
    {
        var scene = EditorSceneManager.NewPreviewScene();
        GameObject owner = null, body = null;
        Avatar avatar = null;
        PlayableGraph graph = default;
        PersonaSpawner spawner = null;
        Animator animator = null;
        Material proxyMaterial = null;
        try
        {
            body = new GameObject("__PresenceBodyRoot");
            SceneManager.MoveGameObjectToScene(body, scene);
            var imported = (GameObject)PrefabUtility.InstantiatePrefab(
                AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Models/human/Human.fbx"), scene);
            imported.transform.SetParent(body.transform, false);
            foreach (var skin in body.GetComponentsInChildren<SkinnedMeshRenderer>(true)) skin.updateWhenOffscreen = true;
            owner = new GameObject("__PresenceRig");
            SceneManager.MoveGameObjectToScene(owner, scene);
            var reference = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(PersonaReferencePoseSetup.AssetPath);
            Require(PersonaHumanoid.TryPrepare(body.transform, out var skeleton, out avatar, out var error, null, reference), error);
            animator = skeleton.GetComponent<Animator>();
            if (animator == null) animator = skeleton.gameObject.AddComponent<Animator>();
            animator.avatar = avatar;
            animator.applyRootMotion = false;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            var clip = AssetDatabase.LoadAllAssetsAtPath(PersonaArrivalSetup.AnimationFolder + "/Sitting Idle.fbx")
                .OfType<AnimationClip>().First(c => c.isHumanMotion && !c.name.StartsWith("__preview__"));
            graph = PlayableGraph.Create("PresenceRigCheck");
            graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
            var playable = AnimationClipPlayable.Create(graph, clip);
            AnimationPlayableOutput.Create(graph, "pose", animator).SetSourcePlayable(playable);
            graph.Play(); playable.SetTime(.7f); graph.Evaluate(0f);

            spawner = owner.AddComponent<PersonaSpawner>();
            spawner.enabled = false;
            spawner.breathe = spawner.gaze = false;
            spawner.posedExternally = true;
            var arrival = owner.AddComponent<PersonaArrival>();
            arrival.enabled = false;
            arrival.phase = PersonaArrival.Phase.앉음;
            spawner.arrival = arrival;
            spawner.facingSource = () => skeleton.forward;
            Set(spawner, "_spawnedInstance", body);
            Set(spawner, "_presentationStarted", true);
            typeof(PersonaSpawner).GetMethod("FindBreathBones", Private).Invoke(spawner, new object[] { body });
            var late = typeof(PersonaSpawner).GetMethod("LateUpdate", Private);
            var nod = (PersonaListeningNod)typeof(PersonaArrival).GetField("_listeningNod", Private).GetValue(arrival);
            var head = spawner.HeadTransform;
            Require(head != null, "공통 FBX 머리 본 누락");
            // 몸체 검토용 중립 머리다. 등록 인물의 얼굴·생성 GLB를 촬영하지 않는다.
            var proxy = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            SceneManager.MoveGameObjectToScene(proxy, scene);
            proxy.transform.position = head.position + Vector3.up * .08f;
            proxy.transform.localScale = new Vector3(.18f, .24f, .2f);
            proxy.transform.SetParent(head, true);
            proxyMaterial = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            proxyMaterial.SetColor("_BaseColor", new Color(.55f, .66f, .73f));
            proxy.GetComponent<Renderer>().sharedMaterial = proxyMaterial;
            Quaternion original = head.rotation;
            Vector3 rootPosition = body.transform.position;
            var bones = body.GetComponentsInChildren<Transform>(true);
            var positions = bones.Select(b => b.localPosition).ToArray();
            CaptureRig(scene, head, "nod_before.png");
            nod.ConfirmSpeech(2f);
            nod.Tick(0f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            nod.Tick(.45f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            for (int frame = 0; frame < 240; frame++)
            {
                graph.Evaluate(0f);
                late.Invoke(spawner, null);
                Require(Mathf.Abs(Quaternion.Angle(original, head.rotation) - 4.5f) < .03f,
                    "실제 Humanoid 머리 회전 누적 또는 과도한 꺾임");
                for (int i = 0; i < bones.Length; i++)
                    Require(Vector3.Distance(positions[i], bones[i].localPosition) < .00001f, "끄덕임이 본 위치를 변경함");
            }
            Require(Vector3.Distance(rootPosition, body.transform.position) < .00001f, "끄덕임이 몸체 루트를 이동함");
            CaptureRig(scene, head, "nod_peak.png");
            nod.Tick(.5f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            graph.Evaluate(0f); late.Invoke(spawner, null);
            Require(Quaternion.Angle(original, head.rotation) < .03f, "실제 Humanoid 원래 자세 복귀 실패");
            CaptureRig(scene, head, "nod_after.png");
            r.passed.Add("공통 FBX 실제 Humanoid: 240프레임 4.5도 유지·본/루트 위치 보존·원래 자세 복귀");

            spawner.breathe = spawner.gaze = true;
            spawner.seatedHeadLiftDeg = 15f;
            Set(spawner, "_seatedHeadLift", 15f);
            var target = new GameObject("__PresenceLookTarget");
            target.transform.SetParent(owner.transform, false);
            target.transform.position = head.position + Vector3.forward * 2f + Vector3.up * .3f;
            spawner.gazeTarget = target.transform;
            for (int i = 0; i < 120; i++) { graph.Evaluate(0f); late.Invoke(spawner, null); }
            Quaternion combined = head.rotation;
            nod.ConfirmSpeech(2f);
            nod.Tick(0f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            nod.Tick(.45f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            for (int i = 0; i < 240; i++)
            {
                graph.Evaluate(0f); late.Invoke(spawner, null);
                Require(Quaternion.Angle(combined, head.rotation) < 6f, "시선·호흡과 합쳐 회전이 누적됨");
                for (int b = 0; b < bones.Length; b++)
                    Require(Vector3.Distance(positions[b], bones[b].localPosition) < .00001f, "동시 재생이 본 위치를 변경함");
            }
            nod.Tick(.5f, false, false, 1.2f, 4.5f, 4.5f, .9f);
            graph.Evaluate(0f); late.Invoke(spawner, null);
            Require(Quaternion.Angle(combined, head.rotation) < .25f, "시선·호흡과 동시 재생 후 기울기 잔류");
            r.passed.Add("호흡·시선·15도 착석 보정과 끄덕임 동시 재생·복귀");
        }
        finally
        {
            if (graph.IsValid()) graph.Destroy();
            if (animator != null) animator.avatar = null;
            if (avatar != null) UnityEngine.Object.DestroyImmediate(avatar);
            if (proxyMaterial != null) UnityEngine.Object.DestroyImmediate(proxyMaterial);
            if (spawner != null) Set(spawner, "_spawnedInstance", null);
            EditorSceneManager.ClosePreviewScene(scene);
        }
    }

    static void CaptureRig(Scene scene, Transform head, string name)
    {
        var cameraObject = new GameObject("__PresenceRigCamera");
        var lightObject = new GameObject("__PresenceRigLight");
        SceneManager.MoveGameObjectToScene(cameraObject, scene);
        SceneManager.MoveGameObjectToScene(lightObject, scene);
        var camera = cameraObject.AddComponent<Camera>();
        camera.scene = scene;
        camera.cameraType = CameraType.Preview;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(.17f, .19f, .23f);
        camera.fieldOfView = 32;
        camera.nearClipPlane = .05f;
        camera.farClipPlane = 20;
        camera.GetUniversalAdditionalCameraData().allowXRRendering = false;
        Vector3 target = head.position - Vector3.up * .16f;
        camera.transform.position = target + new Vector3(1.2f, .15f, 1.8f);
        camera.transform.LookAt(target);
        var light = lightObject.AddComponent<Light>();
        light.type = LightType.Directional;
        light.intensity = 1.5f;
        light.transform.rotation = Quaternion.Euler(25, -25, 0);
        var rt = new RenderTexture(512, 512, 24);
        var image = new Texture2D(512, 512, TextureFormat.RGB24, false);
        var previous = RenderTexture.active;
        try
        {
            camera.targetTexture = rt;
            camera.Render();
            RenderTexture.active = rt;
            image.ReadPixels(new Rect(0, 0, 512, 512), 0, 0);
            image.Apply();
            Directory.CreateDirectory(Folder);
            File.WriteAllBytes(Path.Combine(Folder, name), image.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = previous;
            camera.targetTexture = null;
            UnityEngine.Object.DestroyImmediate(rt);
            UnityEngine.Object.DestroyImmediate(image);
            UnityEngine.Object.DestroyImmediate(cameraObject);
            UnityEngine.Object.DestroyImmediate(lightObject);
        }
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/공간 음향 실제 출력 (Play)")]
    public static async void CaptureOutput()
    {
        if (capturing || !EditorApplication.isPlaying || EditorApplication.isPaused)
        { Debug.LogWarning("[PersonaPresenceCheck] Play 중 체험 시작 전 상태에서 출력 검사를 실행해 주세요."); return; }
        capturing = true;
        var r = new Report { limits = "합성 PCM의 Unity 최종 믹스 캡처. 실제 HMD 청취와 서버 대화는 별도다." };
        bool oldMute = EditorUtility.audioMasterMute;
        try
        {
            var listener = UnityEngine.Object.FindObjectOfType<AudioListener>();
            Require(listener != null && listener.isActiveAndEnabled, "활성 AudioListener가 필요합니다.");
            Require(AudioSettings.speakerMode == AudioSpeakerMode.Stereo, "스테레오 출력에서 검사해 주세요.");
            foreach (var voice in UnityEngine.Object.FindObjectsOfType<DialogueVoiceClient>())
                Require(!voice.ExperienceActive, "진행 중인 체험을 끝낸 뒤 합성 출력 검사를 실행해 주세요.");
            r.editorMuted = oldMute;
            r.listenerPaused = AudioListener.pause;
            r.listenerVolume = AudioListener.volume;
            EditorUtility.audioMasterMute = false; // 합성 검사 동안만 켜고 finally에서 사용자의 음소거를 복원한다.
            foreach (string name in new[] { "left", "right", "front", "flat" })
            {
                using (var f = new Fixture())
                {
                    Vector3 direction = name == "left" ? -listener.transform.right
                        : name == "right" ? listener.transform.right : listener.transform.forward;
                    f.head.position = listener.transform.position + direction * 2f;
                    f.spatial.headOffsetMeters = Vector3.zero;
                    f.spatial.spatialVoice = name != "flat";
                    f.spatial.RefreshSource();
                    // 기존 스트리밍 경로 그대로 재생하고 최종 믹스에서 좌우 차이를 본다.
                    if (f.source.GetComponent<DialogueAudioRenderer>() == null)
                        f.source.AddComponent<DialogueAudioRenderer>();
                    using (var player = new DialogueAudioPlayer(f.audio))
                    {
                        for (int p = 0; p < 15; p++) player.Enqueue(Tone(p * 4800, 4800));
                        player.MarkComplete();
                        var item = new Output { name = name };
                        item.outputRate = AudioSettings.outputSampleRate;
                        var left = new float[2048];
                        var right = new float[2048];
                        long count = 0;
                        for (int block = 0; block < 60; block++)
                        {
                            await NextFrame();
                            AudioListener.GetOutputData(left, 0);
                            AudioListener.GetOutputData(right, 1);
                            if (block >= 10)
                                for (int i = 0; i < left.Length; i++)
                                { item.left += left[i] * left[i]; item.right += right[i] * right[i]; count++; }
                        }
                        item.left = Math.Sqrt(item.left / Math.Max(1, count));
                        item.right = Math.Sqrt(item.right / Math.Max(1, count));
                        item.consumed = player.Consumed;
                        item.callbacks = player.Snapshot().callbacks;
                        item.sourcePlaying = f.audio.isPlaying;
                        item.rendererEnabled = f.source.GetComponent<DialogueAudioRenderer>().isActiveAndEnabled;
                        r.output.Add(item);
                    }
                }
            }
            Require(r.output[0].left > r.output[0].right * 1.2 &&
                    r.output[1].right > r.output[1].left * 1.2, "캐릭터 좌우 위치에 따른 최종 출력 차이가 없습니다.");
            Require(r.output[2].left > .001 && r.output[3].left > .001, "정면 또는 2D 출력이 무음입니다.");
            r.spatializer = AudioSettings.GetSpatializerPluginName();
            r.success = true;
        }
        catch (Exception e) { r.error = e.ToString(); }
        finally
        {
            EditorUtility.audioMasterMute = oldMute;
            capturing = false;
            Write("spatial_output.json", r);
        }
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/공간 음향 실제 출력 (Play)", true)]
    static bool CanCapture() => EditorApplication.isPlaying && !EditorApplication.isPaused && !capturing;

    static async Task NextFrame()
    {
        int frame = Time.frameCount;
        double deadline = EditorApplication.timeSinceStartup + 20;
        while (Time.frameCount == frame)
        {
            if (!EditorApplication.isPlaying || EditorApplication.isPaused || EditorApplication.timeSinceStartup > deadline)
                throw new InvalidOperationException("Play가 멈추거나 다음 프레임을 기다리는 시간이 초과되었습니다.");
            await Task.Delay(1);
        }
    }

    static byte[] Tone(int start, int count)
    {
        var pcm = new byte[count * 2];
        for (int i = 0; i < count; i++)
        {
            // 낮은/중간 주파수를 섞어 HRTF의 방향 차이를 관측한다. 등록 음성은 사용하지 않는다.
            double t = (start + i) / 24000.0;
            short v = (short)(6000 * (Math.Sin(t * 2 * Math.PI * 500) + .4 * Math.Sin(t * 2 * Math.PI * 1700)));
            pcm[i * 2] = (byte)v; pcm[i * 2 + 1] = (byte)(v >> 8);
        }
        return pcm;
    }

    sealed class Fixture : IDisposable
    {
        public readonly GameObject owner, source, body;
        public readonly Transform head;
        public readonly PersonaSpawner spawner;
        public readonly AudioSource audio;
        public readonly PersonaVoiceSpatialAudio spatial;
        public Fixture()
        {
            owner = new GameObject("__PresenceCheck") { hideFlags = HideFlags.HideAndDontSave };
            source = new GameObject("__PresenceVoice") { hideFlags = HideFlags.HideAndDontSave };
            body = new GameObject("__PresenceBody") { hideFlags = HideFlags.HideAndDontSave };
            head = new GameObject("Head").transform;
            head.SetParent(body.transform, false);
            head.position = new Vector3(2f, 1.6f, 1f);
            audio = source.AddComponent<AudioSource>();
            audio.playOnAwake = false;
            var voice = source.AddComponent<DialogueVoiceClient>();
            voice.enabled = false;
            spawner = owner.AddComponent<PersonaSpawner>();
            spawner.enabled = false;
            spawner.voiceClient = voice;
            Set(spawner, "_spawnedInstance", body);
            Set(spawner, "_head", head);
            spatial = owner.AddComponent<PersonaVoiceSpatialAudio>();
        }
        public void RestoreAudio() => typeof(PersonaVoiceSpatialAudio).GetMethod("OnDisable", Private).Invoke(spatial, null);
        public void Dispose()
        {
            RestoreAudio();
            Set(spawner, "_spawnedInstance", null);
            UnityEngine.Object.DestroyImmediate(owner);
            UnityEngine.Object.DestroyImmediate(source);
            UnityEngine.Object.DestroyImmediate(body);
        }
    }
    static void Set(object owner, string field, object value) => owner.GetType().GetField(field, Private).SetValue(owner, value);
    static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    static void Write(string name, Report report)
    {
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Path.Combine(Folder, name), JsonUtility.ToJson(report, true));
        if (report.success) Debug.Log("[PersonaPresenceCheck] " + name + ": PASS");
        else Debug.LogWarning("[PersonaPresenceCheck] " + name + ": FAILED — 원인은 결과 JSON에 보존했습니다.");
    }
}
