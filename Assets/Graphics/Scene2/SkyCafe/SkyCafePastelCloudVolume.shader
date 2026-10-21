Shader "AgainSpring/Sky Cafe Pastel Cloud Volume"
{
    Properties
    {
        [NoScaleOffset] _Volume ("구름 밀도·내부 그늘", 3D) = "white" {}
        [NoScaleOffset] _Noise ("입체 구름 결", 3D) = "white" {}
        [NoScaleOffset] _DetailMap ("구름의 세부 명암", 2D) = "white" {}
        _PaintDetail ("세부 명암 반영", Range(0,1)) = .94
        _PhotoExposure ("세부 명암 밝기", Range(.2,3)) = 1.55
        _PhotoFlip ("세부 명암 좌우 반전", Range(0,1)) = 0
        _Motion ("구름 결 움직임", Range(0,1)) = 1
        _LightColor ("밝은 구름", Color) = (1.10,1.04,1.15,1)
        _MidColor ("구름의 연보라", Color) = (.62,.55,.91,1)
        _ShadeColor ("구름 안쪽의 그늘", Color) = (.23,.20,.56,1)
        _PinkColor ("분홍빛 반사", Color) = (.96,.50,.76,1)
        _PinkAmount ("분홍빛 정도", Range(0,1)) = .42
        [HDR] _RimColor ("구름 윤곽의 빛", Color) = (1.10,.95,1.15,1)
        _Density ("구름 밀도", Range(.1,2)) = .9
        _Absorption ("빛 흡수", Range(.1,2)) = .65
        _Erosion ("가장자리의 작은 결", Range(0,.7)) = .22
        _DetailScale ("입체 결의 크기", Float) = .08
        _Steps ("광선 샘플 수", Range(16,64)) = 56
    }
    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "Queue"="Transparent-40" "RenderType"="Transparent" }
        Pass
        {
            Tags { "LightMode"="UniversalForward" }
            Cull Front ZWrite Off ZTest LEqual
            Blend One OneMinusSrcAlpha
            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            TEXTURE3D(_Volume); SAMPLER(sampler_Volume);
            TEXTURE3D(_Noise); SAMPLER(sampler_Noise);
            TEXTURE2D(_DetailMap); SAMPLER(sampler_DetailMap);
            CBUFFER_START(UnityPerMaterial)
                half4 _LightColor, _MidColor, _ShadeColor, _PinkColor, _RimColor;
                float _Density, _Absorption, _Erosion, _DetailScale, _Steps;
                half _PaintDetail, _PhotoExposure, _PhotoFlip, _Motion, _PinkAmount;
            CBUFFER_END
            struct A { float4 positionOS:POSITION; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct V { float4 positionCS:SV_POSITION; float3 positionWS:TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };
            V Vert(A i)
            {
                V o; UNITY_SETUP_INSTANCE_ID(i); UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionWS=TransformObjectToWorld(i.positionOS.xyz);
                o.positionCS=TransformWorldToHClip(o.positionWS); return o;
            }
            half3 PastelPaint(half3 paint, half lighting, half blush)
            {
                half luminance=dot(paint,half3(.2126,.7152,.0722));
                half tone=saturate(pow(max(luminance,.0001),.45)*_PhotoExposure+lighting*.06);
                half3 color=lerp(_ShadeColor.rgb,_MidColor.rgb,smoothstep(0,.55,tone));
                color=lerp(color,_LightColor.rgb,smoothstep(.35,1,tone));
                half pink=blush*_PinkAmount*(1-tone*.72);
                return lerp(color,lerp(_PinkColor.rgb,_LightColor.rgb,tone),pink);
            }
            half4 Frag(V i):SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                // 밤 버전과 같은 밀도·형상·눈별 광선을 쓰고 별도 재질에서 색을 바꾼다.
                float3 originWS=GetCameraPositionWS();
                float3 directionWS=normalize(i.positionWS-originWS);
                float3 originOS=TransformWorldToObject(originWS);
                float3 directionOS=mul((float3x3)unity_WorldToObject,directionWS);
                float3 safeDirection=lerp(-1.0,1.0,step(0,directionOS))*max(abs(directionOS),1e-6);
                float3 t0=(-.5-originOS)/safeDirection, t1=(.5-originOS)/safeDirection;
                float3 near3=min(t0,t1), far3=max(t0,t1);
                float start=max(0,max(near3.x,max(near3.y,near3.z)));
                float end=min(far3.x,min(far3.y,far3.z));
                if(end<=start) return 0;
                int steps=(int)clamp(_Steps,16,64);
                float stride=(end-start)/steps;
                float t=start+stride*.5;
                half transmission=1;
                half3 accumulated=0;
                half4 surfacePaint=0;
                bool hasPaint=false;
                half blush=.5+.5*sin(originWS.x*.09+originWS.z*.052+1.2);
                float3 drift=float3(_Time.y*.0015,0,_Time.y*.0006)*_Motion;
                [loop] for(int s=0;s<64;s++)
                {
                    if(s>=steps||transmission<.012) break;
                    float3 p=originOS+directionOS*t;
                    half3 volume=SAMPLE_TEXTURE3D_LOD(_Volume,sampler_Volume,p+.5,0).rgb;
                    if(volume.r>.002)
                    {
                        float3 world=originWS+directionWS*t;
                        half2 detail=SAMPLE_TEXTURE3D_LOD(_Noise,sampler_Noise,world*_DetailScale+drift,0).rg;
                        half density=saturate(volume.r-(1-detail.r)*_Erosion)*_Density;
                        if(!hasPaint&&density>.045)
                        {
                            float2 paintUV=p.xy+.5;paintUV.x=lerp(paintUV.x,1-paintUV.x,_PhotoFlip);
                            surfacePaint=SAMPLE_TEXTURE2D_LOD(_DetailMap,sampler_DetailMap,paintUV,0);
                            blush=.5+.5*sin(world.x*.09+world.z*.052+1.2);
                            hasPaint=true;
                        }
                        density*=lerp(1,sqrt(surfacePaint.a),_PaintDetail);
                        half alpha=1-exp(-density*_Absorption*stride);
                        half surface=saturate(1-density);
                        half lighting=saturate(smoothstep(.12,.88,volume.g)*.78+(volume.b-.5)*.28+(detail.g-.5)*surface*.25);
                        half3 color=lerp(_ShadeColor.rgb,_LightColor.rgb,lighting);
                        half silver=pow(volume.b,4)*pow(volume.g,2)*surface;
                        color+=_RimColor.rgb*silver*.46;
                        color=lerp(color,PastelPaint(surfacePaint.rgb,lighting,blush),surfacePaint.a*_PaintDetail);
                        accumulated+=transmission*alpha*color;
                        transmission*=1-alpha;
                    }
                    t+=stride;
                }
                return half4(accumulated,1-transmission);
            }
            ENDHLSL
        }
    }
}
