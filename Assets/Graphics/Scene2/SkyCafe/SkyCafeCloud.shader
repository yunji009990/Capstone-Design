Shader "AgainSpring/Sky Cafe Cloud"
{
    Properties
    {
        _LightColor ("구름 햇빛", Color) = (0.99,0.88,0.82,1)
        _ShadeColor ("구름 속 그늘", Color) = (0.35,0.39,0.57,1)
        _HazeColor ("먼 구름 색", Color) = (0.86,0.78,0.81,1)
        _Detail ("구름 결", Range(0,1)) = 0.25
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "RenderType"="Opaque" "Queue"="Geometry" }
        Pass
        {
            Tags { "LightMode"="UniversalForward" }
            Cull Back ZWrite On
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            CBUFFER_START(UnityPerMaterial)
                half4 _LightColor, _ShadeColor, _HazeColor;
                half _Detail;
            CBUFFER_END
            struct A { float4 positionOS:POSITION; float3 normalOS:NORMAL; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float3 positionWS:TEXCOORD0; half3 normalWS:TEXCOORD1; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionWS=TransformObjectToWorld(i.positionOS.xyz); o.positionCS=TransformWorldToHClip(o.positionWS);
                o.normalWS=TransformObjectToWorldNormal(i.normalOS); return o;
            }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                half3 n=normalize(i.normalWS);
                float3 p=i.positionWS*.62;
                half detail=sin(p.x+sin(p.z*1.4))*sin(p.y*1.3+sin(p.x*.73))*.5;
                half light=saturate(dot(n,normalize(float3(.45,.75,.42)))*.55+.42+detail*_Detail*.24);
                half3 col=lerp(_ShadeColor.rgb,_LightColor.rgb,smoothstep(0,1,light));
                float distance=length(i.positionWS-GetCameraPositionWS());
                col=lerp(col,_HazeColor.rgb,smoothstep(30,90,distance)*.78);
                return half4(col,1);
            }
            ENDHLSL
        }
    }
}
