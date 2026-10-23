using System;
using UnityEngine;

/// <summary>모델 파일의 단위와 관계없이 -1~1로 저장하는 체형 설정. 0은 제작 원본이다.</summary>
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
