using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Video;

public class ConditionClass : MonoBehaviour
{

    protected bool state = false;
    string type = "";
    public float time = 0;
    int count = 0;
    public void AddCount(){
        count++;
    }
    public int GetCount(){
        return count;
    }
    public bool GetState(){
        return state;
    }

    // object.GetType()을 의도적으로 가리는 게 아니라 멤버 필드 'type'을 노출하는 별도 메서드.
    // CS0108 경고를 막기 위해 명시적으로 new 키워드 사용. (호출부가 없어 시그니처 유지는 안전)
    public new string GetType(){
        return type;
    }

    public float GetTime(){
        return time;
    }

    public void SetState(bool b){
        state = b;
    }

    public void SetType(string c){
        type = c;
    }
    public void SetTime(float t){
        time = t;
    }
    public void SetCount(int n){
        count = n;
    }
    public virtual void AniOver(){
    }
    public virtual void AniStart(){
    }

    public void Init(){
        state = false;
        type = "";
        time = 0;
        count = 0;
        AniStart();
    }


}
