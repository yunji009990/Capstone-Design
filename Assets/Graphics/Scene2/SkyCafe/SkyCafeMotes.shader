Shader "AgainSpring/Sky Cafe Motes"
{
    Properties { [HDR] _Color ("기억의 작은 빛", Color) = (1.6,1.05,0.6,1) }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "RenderType"="Transparent" "Queue"="Transparent+10" }
        Pass
        {
            Blend One One ZWrite Off Cull Off
            HLSLPROGRAM
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            CBUFFER_START(UnityPerMaterial) half4 _Color; CBUFFER_END
            struct A { float4 positionOS:POSITION; float2 uv:TEXCOORD0; half4 color:COLOR; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float2 uv:TEXCOORD0; half phase:TEXCOORD1; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                float3 p=i.positionOS.xyz; p.y+=sin(_Time.y*.22+i.color.r*20)*.12;
                o.positionCS=TransformObjectToHClip(p); o.uv=i.uv; o.phase=i.color.r; return o;
            }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                half r=length(i.uv*2-1); half glow=pow(saturate(1-r),3);
                half breathe=.65+.12*sin(_Time.y*.35+i.phase*25);
                return half4(_Color.rgb*glow*breathe,0);
            }
            ENDHLSL
        }
    }
}
