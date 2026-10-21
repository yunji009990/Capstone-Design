Shader "AgainSpring/Sky Cafe Cloud Wisp"
{
    Properties
    {
        [NoScaleOffset] _MainTex ("달빛 구름 RGBA", 2D) = "white" {}
        _Opacity ("구름 밀도", Range(0,1)) = .82
        _Exposure ("구름 밝기", Range(.2,2)) = 1
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "RenderType"="Transparent" "Queue"="Transparent-30" }
        Pass
        {
            Tags { "LightMode"="UniversalForward" }
            Blend One OneMinusSrcAlpha ZWrite Off Cull Off
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            TEXTURE2D(_MainTex); SAMPLER(sampler_MainTex);
            CBUFFER_START(UnityPerMaterial) half _Opacity, _Exposure; CBUFFER_END
            struct A { float4 positionOS:POSITION; float2 uv:TEXCOORD0; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float2 uv:TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                float3 p=i.positionOS.xyz;
                p.y+=sin(_Time.y*.055+unity_ObjectToWorld._m03*.13)*.013;
                o.positionCS=TransformObjectToHClip(p);o.uv=i.uv;return o;
            }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                half4 cloud=SAMPLE_TEXTURE2D(_MainTex,sampler_MainTex,i.uv);
                half alpha=cloud.a*_Opacity;
                return half4(cloud.rgb*_Exposure*alpha,alpha);
            }
            ENDHLSL
        }
    }
}
