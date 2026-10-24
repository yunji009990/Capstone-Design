using System;
using UnityEngine;

/// <summary>체형 Blend Shape와 결합 머리·전체 크기의 공용 설정.</summary>
[CreateAssetMenu(fileName = "PersonaBodyShape", menuName = "다시봄/체형 설정")]
public sealed class PersonaBodyShapeProfile : ScriptableObject
{
    public const string DefaultResourceName = "PersonaBodyShape";
    public const int ShapeCount = 6;

    [SerializeField, Range(-1f, 1f), Tooltip("가슴과 등의 좌우 폭. 음수는 좁게, 양수는 넓게.")]
    float upperBodyWidth;
    [SerializeField, Range(-1f, 1f), Tooltip("상체의 앞뒤 두께. 음수는 얇게, 양수는 두껍게.")]
    float upperBodyDepth;
    [SerializeField, Range(-1f, 1f), Tooltip("허리와 옆구리의 좌우 폭.")]
    float waistWidth;
    [SerializeField, Range(-1f, 1f), Tooltip("배 앞쪽의 볼륨. 음수는 들어가고 양수는 나온다.")]
    float bellyVolume;
    [SerializeField, Range(-1f, 1f), Tooltip("양팔과 소매의 굵기. 손과 소매 안감은 유지한다.")]
    float armThickness;
    [SerializeField, Range(-1f, 1f), Tooltip("양쪽 다리와 바지의 굵기. 신발은 유지한다.")]
    float legThickness;

    [Header("전체 크기 · 머리 결합")]
    [SerializeField, Range(.7f, 1.3f), Tooltip("캐릭터 전체 배율. 1은 현재 키, 0.9는 90%. 리깅도 함께 같은 비율로 바뀐다.")]
    float bodyScale = 1f;
    [SerializeField, Range(.75f, 1.35f), Tooltip("몸에 대한 머리·얼굴·머리카락 크기. 목 연결부를 기준으로 조절한다.")]
    float headScale = 1f;
    [SerializeField, Range(-.03f, .05f), Tooltip("자동 목 연결 보정에 더하는 머리 높이. 몸체 원본 높이의 비율로, 0.01은 1% 위로.")]
    float headHeight;
    [SerializeField, Range(-.04f, .04f), Tooltip("머리의 앞뒤 위치. 몸체 원본 높이의 비율. 양수는 얼굴 방향, 음수는 뒤쪽.")]
    float headForward;

    public float BodyScale => Finite(bodyScale, 1f, .7f, 1.3f);
    public float HeadScale => Finite(headScale, 1f, .75f, 1.35f);
    public float HeadHeight => Finite(headHeight, 0f, -.03f, .05f);
    public float HeadForward => Finite(headForward, 0f, -.04f, .04f);
    static float Finite(float value, float fallback, float min, float max) =>
        float.IsNaN(value) || float.IsInfinity(value) ? fallback : Mathf.Clamp(value, min, max);

    public static PersonaBodyShapeProfile LoadDefault() =>
        Resources.Load<PersonaBodyShapeProfile>(DefaultResourceName);

    public static string ShapeName(int index)
    {
        switch (index)
        {
            case 0: return "상체_폭";
            case 1: return "상체_두께";
            case 2: return "허리_폭";
            case 3: return "배_볼륨";
            case 4: return "팔_굵기";
            case 5: return "다리_굵기";
            default: throw new ArgumentOutOfRangeException(nameof(index));
        }
    }

    public float GetValue(int index)
    {
        float value;
        switch (index)
        {
            case 0: value = upperBodyWidth; break;
            case 1: value = upperBodyDepth; break;
            case 2: value = waistWidth; break;
            case 3: value = bellyVolume; break;
            case 4: value = armThickness; break;
            case 5: value = legThickness; break;
            default: throw new ArgumentOutOfRangeException(nameof(index));
        }
        return float.IsNaN(value) || float.IsInfinity(value) ? 0f : Mathf.Clamp(value, -1f, 1f);
    }
}
