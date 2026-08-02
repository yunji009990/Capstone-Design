// PersonaSpawner.cs
// "다시, 봄" — 웹에서 등록한 인물의 3D 모델(.glb)을 Raon 서버에서 받아
// 고정된 스폰포인트에 배치한다.
//
// 의존성: com.unity.cloud.gltfast (Packages/manifest.json에 이미 추가됨)
//
// 데이터 흐름
//   1. 웹에서 설문·사진·음성을 올려 등록한다.
//   2. 웹 백엔드가 3D 모델을 만들어 서버의 /session/{id}/model 로 올린다.
//   3. 본 스크립트가 /session/current 를 지켜보다가 has_model 이 되면
//      /session/{id}/model.glb 를 받아 glTFast 로 띄운다.
//
// 예전에는 Streamlit 이 쓰던 로컬 폴더(data/sessions/<id>/model.glb)를 직접 읽었다.
// 빌드해서 VR 기기에 넣으면 그 경로가 없어서 아무것도 안 뜬다. 서버에서 받아야 한다.
//
// 셋업
//   1. 빈 GameObject 두 개: "PersonaSpawner"(부모) + 그 자식으로 "SpawnPoint".
//   2. SpawnPoint 의 position/rotation 으로 인물이 등장할 자리·방향을 잡는다.
//   3. PersonaSpawner 에 이 스크립트를 부착하고 spawnPoint 를 연결한다.
//   4. 같은 씬에 RaonVoiceClient 가 있으면 주소·토큰·세션을 자동으로 따라간다.
//      없으면 인스펙터의 serverUrl/token 을 쓴다.

using System;
using System.Collections;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;
using UnityEngine.Networking;

[DisallowMultipleComponent]
public class PersonaSpawner : MonoBehaviour
{
    [Header("서버")]
    [Tooltip("같은 씬의 RaonVoiceClient. 비워두면 자동으로 찾는다.\n" +
             "찾으면 주소·토큰·세션을 그쪽에 맞춘다 — 두 곳에 따로 적으면 어긋난다.")]
    public RaonVoiceClient voiceClient;

    [Tooltip("RaonVoiceClient 가 없을 때 쓸 서버 주소.")]
    public string serverUrl = "http://220.69.208.201:8000";

    [Tooltip("RaonVoiceClient 가 없을 때 쓸 토큰.")]
    public string token = "";

    [Tooltip("강제로 사용할 세션 ID. 비워두면 서버의 현재 세션을 따라간다.")]
    public string sessionIdOverride = "";

    [Header("스폰")]
    [Tooltip("인물이 등장할 위치/회전. 비워두면 본 GameObject 자신을 사용.")]
    public Transform spawnPoint;

    [Tooltip("로드된 모델을 이 높이(m)로 자동 스케일. 0이면 GLB 원본 크기.")]
    public float targetHeightMeters = 1.7f;

    [Tooltip("최종 스케일에 곱하는 추가 배수. 예: 0.1 = 10%.")]
    public float scaleMultiplier = 1.0f;

    [Tooltip("spawnPoint 의 회전을 그대로 사용할지 여부.")]
    public bool useSpawnRotation = true;

    [Header("대기")]
    [Tooltip("모델이 아직 없을 때 다시 물어보는 간격(초). 생성에 몇 분 걸린다.")]
    public float retryIntervalSec = 3f;

    [Tooltip("이 시간(초) 안에 모델이 오지 않으면 포기. 0이면 무한 대기.")]
    public float maxWaitSec = 0f;

    [Header("디버그 / UI (선택)")]
    [Tooltip("모델을 기다리는 동안 켜져 있다가 완료 시 자동으로 꺼지는 GameObject.")]
    public GameObject loadingIndicator;

    public bool verboseLog = true;

    // ── 내부 상태 ──────────────────────────────────────────────
    GameObject _spawnedInstance;
    string _loadedSession = "";      // 이미 띄운 세션. 인물이 바뀔 때만 다시 받는다.
    bool _isLoading;

    string Url => voiceClient != null ? voiceClient.serverUrl : serverUrl;
    string Tok => voiceClient != null ? voiceClient.token : token;

    void Start()
    {
        if (voiceClient == null) voiceClient = FindObjectOfType<RaonVoiceClient>();
        StartCoroutine(WatchSession());
    }

    /// <summary>
    /// 서버의 현재 세션을 지켜본다. 인물이 바뀌면 새 모델로 갈아 끼운다.
    /// 웹에서 등록하는 순간 VR 안의 인물도 따라 바뀌어야 한다.
    /// </summary>
    IEnumerator WatchSession()
    {
        var wait = new WaitForSeconds(Mathf.Max(1f, retryIntervalSec));
        float waited = 0f;

        while (true)
        {
            string sid = sessionIdOverride.Trim();
            bool hasModel = true;

            if (string.IsNullOrEmpty(sid))
            {
                // RaonVoiceClient 가 이미 세션을 따라가고 있으면 그 값을 쓴다.
                if (voiceClient != null && voiceClient.HasSession)
                {
                    sid = voiceClient.sessionId;
                    hasModel = voiceClient.SessionHasModel;
                }
                else
                {
                    yield return StartCoroutine(FetchCurrent(r => { sid = r.session; hasModel = r.has_model; }));
                }
            }

            // 인물이 바뀌었는데 새 사람에게 모델이 없으면, 앞사람을 치워야 한다.
            // 안 그러면 목소리는 새 사람인데 서 있는 모습은 앞사람이 된다.
            if (!string.IsNullOrEmpty(sid) && sid != _loadedSession && !hasModel && _spawnedInstance)
            {
                Destroy(_spawnedInstance);
                _spawnedInstance = null;
                _loadedSession = "";
                if (verboseLog) Debug.Log($"[PersonaSpawner] 인물이 {sid} 로 바뀌었고 모델이 없어 치웠습니다.");
            }

            if (!string.IsNullOrEmpty(sid) && hasModel && sid != _loadedSession && !_isLoading)
            {
                yield return StartCoroutine(LoadModel(sid));
                waited = 0f;
            }
            else if (maxWaitSec > 0 && string.IsNullOrEmpty(_loadedSession))
            {
                waited += retryIntervalSec;
                if (waited >= maxWaitSec)
                {
                    Debug.LogWarning($"[PersonaSpawner] {maxWaitSec}초 안에 모델이 오지 않았습니다.");
                    yield break;
                }
            }
            yield return wait;
        }
    }

    IEnumerator FetchCurrent(Action<SessionResponse> onOk)
    {
        using (var req = UnityWebRequest.Get($"{Url}/session/current"))
        {
            if (!string.IsNullOrEmpty(Tok)) req.SetRequestHeader("X-Token", Tok);
            req.timeout = 10;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success) yield break;
            SessionResponse s = null;
            try { s = JsonUtility.FromJson<SessionResponse>(req.downloadHandler.text); }
            catch (Exception e) { Debug.LogWarning($"[PersonaSpawner] 세션 응답 파싱 실패: {e.Message}"); }
            if (s != null) onOk(s);
        }
    }

    /// <summary>모델을 내려받아 띄운다. 파일로 저장하지 않고 메모리에서 바로 읽는다.</summary>
    IEnumerator LoadModel(string sid)
    {
        _isLoading = true;
        if (loadingIndicator) loadingIndicator.SetActive(true);

        byte[] glb = null;
        using (var req = UnityWebRequest.Get($"{Url}/session/{UnityWebRequest.EscapeURL(sid)}/model.glb"))
        {
            if (!string.IsNullOrEmpty(Tok)) req.SetRequestHeader("X-Token", Tok);
            req.timeout = 120;
            if (verboseLog) Debug.Log($"[PersonaSpawner] 모델 받는 중: {sid}");
            yield return req.SendWebRequest();

            // 404 는 아직 안 만들어졌다는 뜻이라 정상이다. 다음 회차에 다시 묻는다.
            if (req.result != UnityWebRequest.Result.Success)
            {
                if (req.responseCode != 404)
                    Debug.LogWarning($"[PersonaSpawner] 모델 요청 실패 {req.responseCode}: {req.error}");
                Finish();
                yield break;
            }
            glb = req.downloadHandler.data;
        }

        if (glb == null || glb.Length == 0)
        {
            Debug.LogWarning("[PersonaSpawner] 받은 모델이 비어 있습니다.");
            Finish();
            yield break;
        }

        // glTFast 는 async/await 라 코루틴에서 완료를 기다린다.
        var task = SpawnAsync(sid, glb);
        while (!task.IsCompleted) yield return null;
        if (task.IsFaulted) Debug.LogError($"[PersonaSpawner] 스폰 실패: {task.Exception}");
        Finish();
    }

    void Finish()
    {
        _isLoading = false;
        if (loadingIndicator) loadingIndicator.SetActive(false);
    }

    async Task SpawnAsync(string sid, byte[] glb)
    {
        var gltf = new GltfImport();
        if (!await gltf.LoadGltfBinary(glb))
        {
            Debug.LogError("[PersonaSpawner] GLB 해석 실패");
            return;
        }

        if (_spawnedInstance) Destroy(_spawnedInstance);   // 인물이 바뀌면 이전 것을 치운다

        Transform anchor = spawnPoint != null ? spawnPoint : transform;
        _spawnedInstance = new GameObject($"Persona_{sid}");
        _spawnedInstance.transform.SetPositionAndRotation(
            anchor.position, useSpawnRotation ? anchor.rotation : Quaternion.identity);

        if (!await gltf.InstantiateMainSceneAsync(_spawnedInstance.transform))
        {
            Debug.LogError("[PersonaSpawner] 장면 생성 실패");
            return;
        }

        if (targetHeightMeters > 0f) NormalizeHeight(_spawnedInstance, targetHeightMeters);
        if (scaleMultiplier > 0f && Mathf.Abs(scaleMultiplier - 1f) > 0.0001f)
            _spawnedInstance.transform.localScale *= scaleMultiplier;

        _loadedSession = sid;
        if (verboseLog)
            Debug.Log($"[PersonaSpawner] 스폰 완료: {sid} at {anchor.position}, " +
                      $"scale={_spawnedInstance.transform.localScale}");
    }

    [Serializable]
    class SessionResponse
    {
        public string session;
        public bool has_model;
    }

    static void NormalizeHeight(GameObject root, float targetHeight)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0) return;

        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++) bounds.Encapsulate(renderers[i].bounds);
        if (bounds.size.y <= 0.0001f) return;

        root.transform.localScale *= targetHeight / bounds.size.y;
    }
}
