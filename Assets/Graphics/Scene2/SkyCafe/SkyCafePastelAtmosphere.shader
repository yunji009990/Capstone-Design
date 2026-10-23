Shader "AgainSpring/Sky Cafe Pastel Atmosphere"
{
    Properties
    {
        _Zenith ("높은 하늘의 푸른빛", Color) = (.10,.30,.95,1)
        _Horizon ("낮은 하늘의 푸른빛", Color) = (.30,.48,1,1)
        _CloudColor ("옅은 흰 구름", Color) = (1.02,.98,1.10,1)
        _RoseColor ("은은한 분홍빛", Color) = (1,.61,.81,1)
        _CloudAmount ("높은 하늘의 구름", Range(0,1)) = .85
        _Exposure ("하늘 밝기", Range(.2,2)) = 1
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off ZWrite Off
        Pass
        {
            HLSLPROGRAM
            #pragma target 3.5
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            CBUFFER_START(UnityPerMaterial)
                half4 _Zenith, _Horizon, _CloudColor, _RoseColor;
                half _CloudAmount, _Exposure;
            CBUFFER_END
            struct A { float4 positionOS:POSITION; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float3 direction:TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionCS=TransformObjectToHClip(i.positionOS.xyz);
                o.direction=TransformObjectToWorldDir(i.positionOS.xyz); return o;
            }
            float Hash(float3 p) { p=frac(p*.1031); p+=dot(p,p.yzx+33.33); return frac((p.x+p.y)*p.z); }
            float Noise(float3 p)
            {
                float3 a=floor(p),f=frac(p);f=f*f*(3-2*f);
                return lerp(lerp(lerp(Hash(a),Hash(a+float3(1,0,0)),f.x),lerp(Hash(a+float3(0,1,0)),Hash(a+float3(1,1,0)),f.x),f.y),
                    lerp(lerp(Hash(a+float3(0,0,1)),Hash(a+float3(1,0,1)),f.x),lerp(Hash(a+float3(0,1,1)),Hash(a+1),f.x),f.y),f.z);
            }
            float Fbm(float3 p) { return Noise(p)*.52+Noise(p*2.03+17)*.28+Noise(p*4.13+31)*.14+Noise(p*8.29)*.06; }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                float3 d=normalize(i.direction);
                half3 sky=lerp(_Horizon.rgb,_Zenith.rgb,pow(saturate(d.y+.035),.48));
                float blush=Fbm(d*3.2+float3(4,11,19));
                sky=lerp(sky,_RoseColor.rgb,smoothstep(.42,.74,blush)*.28*(1-saturate(d.y)));
                // 먼 하늘에만 옅은 높은 구름을 넣고 가까운 구름은 별도 3D 밀도로 그린다.
                float3 p=d*float3(7.2,14,7.2)+float3(5,8,19);
                float field=Fbm(p)*.78+Fbm(p*2.7+7)*.22;
                float wisps=smoothstep(.40,.68,field)*smoothstep(.015,.28,d.y);
                half3 cloud=lerp(_CloudColor.rgb,_RoseColor.rgb,smoothstep(.38,.72,blush)*.24);
                sky=lerp(sky,cloud,wisps*_CloudAmount);
                return half4(sky*_Exposure,1);
            }
            ENDHLSL
        }
    }
}
