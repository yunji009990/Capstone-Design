using UnityEngine;
public class SceneIndexPlus : MonoBehaviour
{
    public void IncrementSceneIndex()
    {
        // 싱글톤으로 선언된 CSVWriter의 함수를 대신 호출해줍니다.
        if (CSVWriter.Instance != null)
        {
            CSVWriter.Instance.IncrementSceneIndex();
        }
        else
        {
            Debug.LogError("CSVWriter Instance를 찾을 수 없습니다!");
        }
    }
}