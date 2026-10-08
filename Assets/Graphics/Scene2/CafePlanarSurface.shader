Shader "AgainSpring/Cafe Planar Surface"
{
    Properties
    {
        _BaseColor ("옅은 유리 색", Color) = (0.96,0.985,1,0.003)
        _ReflectionOpacity ("정면 반사", Range(0,1)) = 0.006
        _GrazingOpacity ("비스듬한 반사", Range(0,1)) = 0.035
        _ReflectionGain ("반사 밝기", Range(0,1)) = 0.6
        _HighlightCompression ("밝은 반사 완화", Range(0,1)) = 0
        [Enum(UnityEngine.Rendering.CullMode)] _Cull ("표면 컬링", Float) = 2
        [HideInInspector] _PlanarLeft ("Left", 2D) = "black" {}
        [HideInInspector] _PlanarRight ("Right", 2D) = "black" {}
        [HideInInspector] _PlanarValid ("Valid", Float) = 0
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "RenderType"="Transparent" "Queue"="Transparent" }
        Pass
        {
            Name "PlanarSurface"
            Tags { "LightMode"="UniversalForward" }
            Blend One OneMinusSrcAlpha
            ZWrite Off
            Cull [_Cull]
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            TEXTURE2D(_PlanarLeft); SAMPLER(sampler_PlanarLeft);
            TEXTURE2D(_PlanarRight); SAMPLER(sampler_PlanarRight);
            CBUFFER_START(UnityPerMaterial)
                half4 _BaseColor;
                half _ReflectionOpacity, _GrazingOpacity, _ReflectionGain, _PlanarValid;
                half _HighlightCompression;
                float4x4 _PlanarMatrixLeft, _PlanarMatrixRight;
            CBUFFER_END
            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS : NORMAL;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };
            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float3 positionWS : TEXCOORD0;
                half3 normalWS : TEXCOORD1;
                UNITY_VERTEX_OUTPUT_STEREO
            };
            Varyings Vert(Attributes input)
            {
                Varyings output;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);
                output.positionWS = TransformObjectToWorld(input.positionOS.xyz);
                output.positionCS = TransformWorldToHClip(output.positionWS);
                output.normalWS = TransformObjectToWorldNormal(input.normalOS);
                return output;
            }
            half4 Frag(Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);
                float4 projected = mul(_PlanarMatrixLeft, float4(input.positionWS, 1));
                half3 reflected;
                #if defined(USING_STEREO_MATRICES)
                if (unity_StereoEyeIndex == 1)
                {
                    projected = mul(_PlanarMatrixRight, float4(input.positionWS, 1));
                    float2 uv = projected.xy / projected.w * 0.5 + 0.5;
                    reflected = SAMPLE_TEXTURE2D(_PlanarRight, sampler_PlanarRight, uv).rgb;
                }
                else
                #endif
                {
                    float2 uv = projected.xy / projected.w * 0.5 + 0.5;
                    reflected = SAMPLE_TEXTURE2D(_PlanarLeft, sampler_PlanarLeft, uv).rgb;
                }
                // 강한 간판 발광이 약한 벽 반사에서도 흰색으로 포화되지 않게 한다.
                half peak = max(reflected.r, max(reflected.g, reflected.b));
                reflected *= rcp(1 + peak * _HighlightCompression);
                half facing = abs(dot(normalize(input.normalWS), GetWorldSpaceNormalizeViewDir(input.positionWS)));
                half fresnel = pow(1 - saturate(facing), 5);
                half opacity = lerp(_ReflectionOpacity, _GrazingOpacity, fresnel) * _PlanarValid;
                half alpha = opacity + _BaseColor.a * (1 - opacity);
                return half4(reflected * opacity * _ReflectionGain + _BaseColor.rgb * _BaseColor.a * (1 - opacity), alpha);
            }
            ENDHLSL
        }
    }
}
