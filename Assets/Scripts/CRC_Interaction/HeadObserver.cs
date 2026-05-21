using System.Collections.Generic;
using UnityEngine;

/*
 * HeadObserver는 사용자의 머리에 대한 회전값을 저장합니다.
 * HeadObserver saves user's head lotation value.
 */
public class HeadObserver : MonoBehaviour
{
    [SerializeField]
    private GameObject centerEyeAnchor; // 기준이 되는 오브젝트.  Standard object.

    private List<string> colnames = new List<string> { "head_roll", "head_pitch", "head_yaw" }; // csv에 저장할 열 이름. column names 
    private List<string> csvData = new List<string> { "0.0", "0.0", "0.0" };

    private void Update()
    {
        csvData[0] = centerEyeAnchor.transform.eulerAngles.z.ToString(); // roll
        csvData[1] = centerEyeAnchor.transform.eulerAngles.x.ToString(); // pitch
        csvData[2] = centerEyeAnchor.transform.eulerAngles.y.ToString(); // yaw
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
