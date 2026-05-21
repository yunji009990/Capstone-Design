using System.Collections.Generic;
using UnityEngine;
using FE = OVRFaceExpressions.FaceExpression;

/*
 * Oculus에서 제공하는 FaceExpressions(퀘스트 프로 착용자의 얼굴에 대한 센서값 담고 있음)를 가지고 데이터를 가져와 저장하는 클래스.
 * Class saving data from FaceExpression(has face sensor data from vr headset user).
 * https://developer.oculus.com/documentation/unity/move-face-tracking/?locale=ko_KR
 */
public class FaceObserver : MonoBehaviour
{
    [SerializeField]
    private OVRFaceExpressions faceExpressions;

    private List<string> colnames = new List<string> {
        "brow_lowerer_l", "brow_lowerer_r",
        "cheek_puff_l", "cheek_puff_r",
        "cheek_raiser_l", "cheek_raiser_r",
        "cheek_suck_l", "cheek_suck_r",
        "chin_raiser_b", "chin_raiser_t",
        "dimpler_l", "dimpler_r",
        "eyes_look_down_l", "eyes_look_down_r",
        "eyes_look_left_l", "eyes_look_left_r",
        "eyes_look_right_l", "eyes_look_right_r",
        "eyes_look_up_l", "eyes_look_up_r",
        "inner_brow_raiser_l", "inner_brow_raiser_r",
        "jaw_drop",
        "jaw_sideways_left", "jaw_sideways_right",
        "jaw_thrust",
        "lid_tightener_l", "lid_tightener_r",
        "lip_corner_depressor_l", "lip_corner_depressor_r",
        "lip_corner_puller_l", "lip_corner_puller_r",
        "lip_funneler_lb", "lip_funneler_lt", "lip_funneler_rb", "lip_funneler_rt",
        "lip_pressor_l", "lip_pressor_r",
        "lip_pucker_l", "lip_pucker_r",
        "lip_stretcher_l", "lip_stretcher_r",
        "lip_suck_lb", "lip_suck_lt", "lip_suck_rb", "lip_suck_rt",
        "lip_tightener_l", "lip_tightener_r",
        "lips_toward",
        "lower_lip_depressor_l", "lower_lip_depressor_r",
        "mouth_left", "mouth_right",
        "nose_wrinkler_l", "nose_wrinkler_r",
        "outer_brow_raiser_l", "outer_brow_raiser_r",
        "upper_lid_raiser_l", "upper_lid_raiser_r",
        "upper_lip_raiser_l", "upper_lip_raiser_r"
    }; // csv에 저장할 열 이름. column names 
    private List<string> csvData = new List<string> { };

    // Start is called before the first frame update
    void Start()
    {
        // 초기 csv 데이터 초기화.
        // Initialize inital csv data.
        for (int i = 0; i < colnames.Count; i++)
            csvData.Add("0.0");
    }

    private void Update()
    {
        // 얼굴 센서값을 가져와 저장.
        // Get face sensor data and save it.
        csvData[0] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.BrowLowererL).ToString() : "0.0";
        csvData[1] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.BrowLowererR).ToString() : "0.0";
        csvData[2] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekPuffL).ToString() : "0.0";
        csvData[3] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekPuffR).ToString() : "0.0";
        csvData[4] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekRaiserL).ToString() : "0.0";
        csvData[5] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekRaiserR).ToString() : "0.0";
        csvData[6] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekSuckL).ToString() : "0.0";
        csvData[7] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.CheekSuckR).ToString() : "0.0";
        csvData[8] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.ChinRaiserB).ToString() : "0.0";
        csvData[9] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.ChinRaiserT).ToString() : "0.0";
        csvData[10] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.DimplerL).ToString() : "0.0";
        csvData[11] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.DimplerR).ToString() : "0.0";
        csvData[12] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookDownL).ToString() : "0.0";
        csvData[13] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookDownR).ToString() : "0.0";
        csvData[14] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookLeftL).ToString() : "0.0";
        csvData[15] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookLeftR).ToString() : "0.0";
        csvData[16] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookRightL).ToString() : "0.0";
        csvData[17] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookRightR).ToString() : "0.0";
        csvData[18] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookUpL).ToString() : "0.0";
        csvData[19] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.EyesLookUpR).ToString() : "0.0";
        csvData[20] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.InnerBrowRaiserL).ToString() : "0.0";
        csvData[21] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.InnerBrowRaiserR).ToString() : "0.0";
        csvData[22] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.JawDrop).ToString() : "0.0";
        csvData[23] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.JawSidewaysLeft).ToString() : "0.0";
        csvData[24] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.JawSidewaysRight).ToString() : "0.0";
        csvData[25] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.JawThrust).ToString() : "0.0";
        csvData[26] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LidTightenerL).ToString() : "0.0";
        csvData[27] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LidTightenerR).ToString() : "0.0";
        csvData[28] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipCornerDepressorL).ToString() : "0.0";
        csvData[29] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipCornerDepressorR).ToString() : "0.0";
        csvData[30] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipCornerPullerL).ToString() : "0.0";
        csvData[31] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipCornerPullerR).ToString() : "0.0";
        csvData[32] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipFunnelerLB).ToString() : "0.0";
        csvData[33] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipFunnelerLT).ToString() : "0.0";
        csvData[34] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipFunnelerRB).ToString() : "0.0";
        csvData[35] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipFunnelerRT).ToString() : "0.0";
        csvData[36] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipPressorL).ToString() : "0.0";
        csvData[37] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipPressorR).ToString() : "0.0";
        csvData[38] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipPuckerL).ToString() : "0.0";
        csvData[39] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipPuckerR).ToString() : "0.0";
        csvData[40] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipStretcherL).ToString() : "0.0";
        csvData[41] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipStretcherR).ToString() : "0.0";
        csvData[42] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipSuckLB).ToString() : "0.0";
        csvData[43] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipSuckLT).ToString() : "0.0";
        csvData[44] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipSuckRB).ToString() : "0.0";
        csvData[45] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipSuckRT).ToString() : "0.0";
        csvData[46] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipTightenerL).ToString() : "0.0";
        csvData[47] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipTightenerR).ToString() : "0.0";
        csvData[48] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LipsToward).ToString() : "0.0";
        csvData[49] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LowerLipDepressorL).ToString() : "0.0";
        csvData[50] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.LowerLipDepressorR).ToString() : "0.0";
        csvData[51] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.MouthLeft).ToString() : "0.0";
        csvData[52] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.MouthRight).ToString() : "0.0";
        csvData[53] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.NoseWrinklerL).ToString() : "0.0";
        csvData[54] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.NoseWrinklerR).ToString() : "0.0";
        csvData[55] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.OuterBrowRaiserL).ToString() : "0.0";
        csvData[56] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.OuterBrowRaiserR).ToString() : "0.0";
        csvData[57] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.UpperLidRaiserL).ToString() : "0.0";
        csvData[58] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.UpperLidRaiserR).ToString() : "0.0";
        csvData[59] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.UpperLipRaiserL).ToString() : "0.0";
        csvData[60] = faceExpressions.ValidExpressions ? faceExpressions.GetWeight(FE.UpperLipRaiserR).ToString() : "0.0";
    }

    public string[] GetColumnNames()
    {
        return colnames.ToArray();
    }

    public string[] GetCSVData()
    {
        return csvData.ToArray();
    }
}
