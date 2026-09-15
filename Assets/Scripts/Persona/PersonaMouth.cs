// PersonaMouth.cs
// 얼굴 뼈가 없는 Tripo 인물의 턱을 말소리 크기에 맞춰 움직인다 — "입 벙긋".
//
// Tripo 리깅은 몸 관절 41개뿐이고 턱·입 뼈도 블렌드셰이프도 없다(2026-09-15 확인).
// 그래서 로드 때 머리 뼈에 붙은 정점 가운데 입 아래 앞쪽을 골라 "턱 벌림"
// 블렌드셰이프를 코드로 하나 만들고, 매 프레임 음성 RMS 로 그 무게를 정한다.
// 입술이 갈라져 속이 보이진 않고 턱·아랫입술이 소리에 맞춰 오르내린다.
//
// 기준 축은 리깅 뼈에서 잰다. 앞 = 발목→발가락, 위 = 골반→머리. 루트의 회전이나
// 뼈의 로컬 축과 무관하게 같은 결과가 나온다. 정점·바인드포즈는 메시 공간이라
// 현재 자세와 상관없이 만들 수 있고, 델타는 스키닝을 따라 머리와 함께 움직인다.
//
// 입 위치는 코끝에서 잰다. 머리 정점 전체의 높이로 비율을 잡으면 머리카락·정수리가
// 섞여 코 높이에 걸린다(실측: 머리 관절 기준 턱끝 0.01, 코끝 0.054, 정수리 0.177).
// 코끝은 정면 가운데에서 가장 앞으로 튀어나온 점이라 어떤 얼굴이든 안정적으로 잡히고,
// 턱끝은 그 아래로 내려가며 정면 윤곽이 목으로 꺾이는 곳이다.

using System;
using System.Collections.Generic;
using UnityEngine;

public class PersonaMouth : MonoBehaviour
{
    [Serializable]
    public class Settings
    {
        [Tooltip("입선 위치. 코끝=0, 턱끝=1 사이의 비율. 사람 얼굴은 0.4 안팎.")]
        [Range(0.2f, 0.7f)] public float mouthFromNoseRatio = 0.42f;

        // 필드 이름이 초기 버전(얼굴 높이 비율 기준)과 달라서, 그때 저장된 값이 새 의미에 섞이지 않는다.
        [Tooltip("움직이는 폭. 입 높이에서 잰 얼굴 너비 대비.")]
        [Range(0.2f, 1f)] public float mouthWidthRatio = 0.6f;

        [Tooltip("입선 위로 움직임이 사그라드는 높이. 코끝~입선 거리 대비. 1 이면 코끝 바로 아래까지.")]
        [Range(0.1f, 1f)] public float lipFadeRatio = 0.5f;

        [Tooltip("턱이 최대로 내려가는 거리. 코끝~턱끝 거리 대비. 실제 입 벌림은 0.2~0.3.")]
        [Range(0f, 0.6f)] public float jawDropRatio = 0.25f;

        [Tooltip("이 크기(dBFS) 아래는 입을 닫는다.")]
        public float floorDb = -42f;

        [Tooltip("이 크기(dBFS)에서 최대로 벌린다.")]
        public float ceilDb = -14f;

        [Tooltip("벌릴 때 반응 시간(초).")]
        public float attackSec = 0.03f;

        [Tooltip("닫힐 때 반응 시간(초).")]
        public float releaseSec = 0.10f;
    }

    public Settings settings = new Settings();

    /// <summary>0~1 선형 RMS 를 돌려주는 함수. 대화 클라이언트의 재생 버퍼가 준다.</summary>
    public Func<float> levelSource;

    [Tooltip("시험용. 0 보다 크면 음성 대신 이 값으로 벌린 채 둔다. 위치·세기를 맞출 때 쓴다.")]
    [Range(0f, 1f)] public float testOpen = 0f;

    [Tooltip("시험용. 켜면 음성 없이도 말하는 것처럼 무작위로 벙긋거린다.")]
    public bool testTalk = false;

    public bool verboseLog;

    [Tooltip("읽기용. 지금 벌린 정도 0~1. 재생 중 인스펙터에서 움직이는지 본다.")]
    public float openNow;

    /// <summary>블렌드셰이프가 만들어져 움직일 준비가 됐는지.</summary>
    public bool Ready => _renderer != null && _shape >= 0;

    /// <summary>지금 벌린 정도 0~1.</summary>
    public float Open => _open;

    const string ShapeName = "JawOpen";
    SkinnedMeshRenderer _renderer;
    Mesh _baseMesh;      // 블렌드셰이프를 더하기 전 원본. 다시 만들 때 여기서 출발한다
    Mesh _shapedMesh;    // 우리가 만든 사본. 다시 만들면 이전 것은 지운다
    int _shape = -1;
    float _open;
    (float, float, float, float) _built;   // 만들 때 쓴 기하 설정. 바뀌면 다시 만든다

    /// <summary>
    /// 머리 뼈에 붙은 정점으로 턱 벌림 블렌드셰이프를 만든다. 실패하면 false 를 돌려주고
    /// 인물은 그대로 둔다(입만 안 움직인다).
    /// </summary>
    public bool Build()
    {
        _shape = -1;
        _renderer = GetComponentInChildren<SkinnedMeshRenderer>();
        if (_renderer == null || _renderer.sharedMesh == null) return Fail("스킨 메시가 없다");
        if (_baseMesh == null) _baseMesh = _renderer.sharedMesh;
        var mesh = _baseMesh;
        if (!mesh.isReadable) return Fail("메시를 읽을 수 없다(isReadable=false)");

        var bones = _renderer.bones;
        int head = Find(bones, "head"), hip = Find(bones, "hip");
        int lFoot = Find(bones, "l_foot"), lToe = Find(bones, "l_toebase");
        int rFoot = Find(bones, "r_foot"), rToe = Find(bones, "r_toebase");
        if (head < 0 || hip < 0 || lFoot < 0 || lToe < 0 || rFoot < 0 || rToe < 0)
            return Fail("기준 뼈(Head/Hip/Foot/ToeBase)를 찾지 못했다");

        // 바인드포즈의 역행렬이 뼈 원점을 메시 공간으로 보낸다. 자세와 무관하다.
        var bind = mesh.bindposes;
        Vector3 At(int i) => bind[i].inverse.GetColumn(3);
        Vector3 up = (At(head) - At(hip)).normalized;
        Vector3 forward = (At(lToe) - At(lFoot)) + (At(rToe) - At(rFoot));
        forward = Vector3.ProjectOnPlane(forward, up).normalized;
        if (forward.sqrMagnitude < 0.5f) return Fail("발가락 방향으로 앞을 정하지 못했다");
        Vector3 right = Vector3.Cross(up, forward).normalized;
        Vector3 headPos = At(head);

        var verts = mesh.vertices;
        var weights = mesh.boneWeights;
        if (weights == null || weights.Length != verts.Length) return Fail("정점 가중치가 없다");

        // 1) 머리 정점: Head 뼈 가중치 합이 절반 이상. 머리 관절 기준 (위, 옆, 앞) 좌표로 바꾼다.
        var idx = new List<int>(verts.Length / 8);
        var ys = new List<float>(); var xs = new List<float>(); var zs = new List<float>();
        for (int i = 0; i < verts.Length; i++)
        {
            var w = weights[i];
            float sum = (w.boneIndex0 == head ? w.weight0 : 0) + (w.boneIndex1 == head ? w.weight1 : 0)
                      + (w.boneIndex2 == head ? w.weight2 : 0) + (w.boneIndex3 == head ? w.weight3 : 0);
            if (sum < 0.5f) continue;
            Vector3 d = verts[i] - headPos;
            idx.Add(i); ys.Add(Vector3.Dot(d, up)); xs.Add(Vector3.Dot(d, right)); zs.Add(Vector3.Dot(d, forward));
        }
        int n = idx.Count;
        if (n < 100) return Fail("머리 정점이 너무 적다: " + n);

        // 2) 정면 가운데 띠에서 코끝(가장 앞) 을 찾는다. 머리카락은 옆·뒤라 띠에 안 들어온다.
        float xMin = float.MaxValue, xMax = float.MinValue, yMax = float.MinValue, zMin = float.MaxValue;
        for (int k = 0; k < n; k++) { xMin = Mathf.Min(xMin, xs[k]); xMax = Mathf.Max(xMax, xs[k]); yMax = Mathf.Max(yMax, ys[k]); zMin = Mathf.Min(zMin, zs[k]); }
        float headW = xMax - xMin, headH = yMax;                       // 머리 관절에서 정수리까지
        float bandX = (xMin + xMax) * 0.5f, bandHalf = headW * 0.12f;
        int nose = -1;
        for (int k = 0; k < n; k++)
            if (Mathf.Abs(xs[k] - bandX) < bandHalf && (nose < 0 || zs[k] > zs[nose])) nose = k;
        if (nose < 0) return Fail("코끝을 찾지 못했다");
        float noseY = ys[nose], noseZ = zs[nose], noseX = xs[nose];

        // 3) 턱끝: 코끝에서 아래로 내려가며 정면 윤곽(띠 안 최대 앞)이 목으로 꺾이는 곳.
        //    턱은 코끝의 앞쪽 80% 안에 있고, 목은 뒤로 훌쩍 물러난다.
        float step = Mathf.Max(0.001f, headH * 0.03f);
        float depthCut = noseZ - 0.35f * (noseZ - zMin);
        float chinY = noseY;
        for (float y0 = noseY - step; y0 > -headH; y0 -= step)
        {
            float front = float.MinValue;
            for (int k = 0; k < n; k++)
                if (ys[k] >= y0 && ys[k] < y0 + step && Mathf.Abs(xs[k] - noseX) < bandHalf) front = Mathf.Max(front, zs[k]);
            if (front < depthCut) break;   // 정면이 사라졌다 = 턱 아래
            chinY = y0;
        }
        float noseToChin = noseY - chinY;
        if (noseToChin < headH * 0.1f) return Fail($"턱끝을 잡지 못했다(코~턱 {noseToChin:0.000})");

        float mouthY = noseY - settings.mouthFromNoseRatio * noseToChin;
        float lipBand = (noseY - mouthY) * settings.lipFadeRatio;      // 입선 위로 사그라드는 높이
        float chinFade = noseToChin * 0.35f;                           // 턱 아래 목으로 사그라드는 높이
        // 입 높이에서 잰 얼굴 너비(앞쪽 절반만). 머리카락이 옆에 있어도 앞쪽만 재면 뺨~뺨이 된다.
        float wMin = float.MaxValue, wMax = float.MinValue;
        for (int k = 0; k < n; k++)
            if (Mathf.Abs(ys[k] - mouthY) < noseToChin * 0.25f && zs[k] > depthCut) { wMin = Mathf.Min(wMin, xs[k]); wMax = Mathf.Max(wMax, xs[k]); }
        float faceW = Mathf.Max(wMax - wMin, headW * 0.3f);
        float halfWidth = faceW * settings.mouthWidthRatio * 0.5f;
        float drop = noseToChin * settings.jawDropRatio;

        // 4) 델타: 입선 아래(아랫입술·턱)는 내려가고, 입선 위는 코 아래에서 사그라든다.
        //    옆으로는 입 폭 안에서, 앞뒤로는 얼굴 면에서만. 뒤통수·목·머리카락은 그대로.
        var delta = new Vector3[verts.Length];
        Vector3 move = -up * drop - forward * (drop * 0.35f);   // 아래로, 살짝 뒤로(턱이 젖혀지듯)
        int touched = 0;
        for (int k = 0; k < n; k++)
        {
            float y = ys[k], x = xs[k], z = zs[k];
            if (y > noseY || y < chinY - chinFade) continue;
            float wSide = 1f - Mathf.Clamp01(Mathf.Abs(x - noseX) / halfWidth);
            float wDepth = Mathf.Clamp01((z - depthCut) / Mathf.Max(0.0001f, noseZ - depthCut) + 0.5f);
            float wUp = y <= mouthY ? 1f : 1f - Mathf.Clamp01((y - mouthY) / lipBand);
            float wLow = y >= chinY ? 1f : 1f - Mathf.Clamp01((chinY - y) / chinFade);
            float w = Smooth(wSide) * Smooth(wDepth) * Smooth(wUp) * Smooth(wLow);
            if (w <= 0.001f) continue;
            delta[idx[k]] = move * w;
            touched++;
        }
        if (touched < 20) return Fail("턱 정점을 찾지 못했다");

        // 5) 메시 사본에 블렌드셰이프를 더한다. 원본 메시는 다른 데서 쓸 수 있으니 건드리지 않는다.
        var copy = Instantiate(mesh);
        copy.name = mesh.name + " (mouth)";
        copy.AddBlendShapeFrame(ShapeName, 100f, delta, null, null);
        _renderer.sharedMesh = copy;
        if (_shapedMesh != null) Destroy(_shapedMesh);
        _shapedMesh = copy;
        _shape = copy.GetBlendShapeIndex(ShapeName);
        _built = Geometry();
        if (verboseLog)
            Debug.Log($"[PersonaMouth] 턱 벌림 준비: 머리 정점 {n}, 움직이는 정점 {touched}, " +
                      $"코끝 y={noseY:0.000} 턱끝 y={chinY:0.000} 입선 y={mouthY:0.000} 폭 {faceW:0.000} 최대 {drop:0.0000}");
        return true;
    }

    static float Smooth(float t) { t = Mathf.Clamp01(t); return t * t * (3f - 2f * t); }

    static int Find(Transform[] bones, string key)
    {
        for (int i = 0; i < bones.Length; i++)
            if (bones[i] != null && bones[i].name.ToLowerInvariant() == key) return i;
        for (int i = 0; i < bones.Length; i++)
            if (bones[i] != null && bones[i].name.ToLowerInvariant().EndsWith(key)) return i;
        return -1;
    }

    bool Fail(string why)
    {
        Debug.LogWarning("[PersonaMouth] 입 벙긋을 만들지 못했다 — " + why);
        return false;
    }

    (float, float, float, float) Geometry() =>
        (settings.mouthFromNoseRatio, settings.mouthWidthRatio, settings.lipFadeRatio, settings.jawDropRatio);

    void LateUpdate()
    {
        if (!Ready) return;
        if (Geometry() != _built) Build();   // 인스펙터에서 위치·세기를 바꾸면 다시 만든다
        if (!Ready) return;

        float target;
        if (testTalk) target = TalkEnvelope(Time.time);
        else if (testOpen > 0f) target = testOpen;
        else
        {
            float rms = levelSource != null ? levelSource() : 0f;
            float db = 20f * Mathf.Log10(Mathf.Max(rms, 1e-5f));
            target = Mathf.Clamp01((db - settings.floorDb) / Mathf.Max(1f, settings.ceilDb - settings.floorDb));
        }
        float tau = target > _open ? settings.attackSec : settings.releaseSec;
        _open = Mathf.Lerp(_open, target, 1f - Mathf.Exp(-Time.deltaTime / Mathf.Max(0.001f, tau)));
        openNow = _open;
        _renderer.SetBlendShapeWeight(_shape, _open * 100f);
    }

    /// <summary>말하는 것처럼 보이는 가짜 포락선. 음절 3~5회/초, 사이사이 쉼.</summary>
    static float TalkEnvelope(float t)
    {
        float phrase = Mathf.PerlinNoise(t * 0.35f, 7.1f);          // 몇 초 단위로 말했다 쉬었다
        if (phrase < 0.42f) return 0f;
        float syllable = Mathf.PerlinNoise(t * 4.2f, 2.9f);         // 음절
        return Mathf.Clamp01((syllable - 0.35f) / 0.4f);
    }

    [ContextMenu("턱 벌림 다시 만들기")]
    void Rebuild() { Build(); }

    void OnDestroy()
    {
        if (_shapedMesh != null) Destroy(_shapedMesh);
    }
}
