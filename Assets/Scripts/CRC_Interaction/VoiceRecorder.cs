using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

/*
 * VoiceRecorder는 퀘스트 프로 내의 마이크로 사용자의 목소리를 녹음하여 wav파일로 저장합니다.
 * VoiceRecorder records user's voice through mic in quest pro and save into wave file.
 */
public class VoiceRecorder : MonoBehaviour
{
    private AudioClip recordingClip; // 녹음본이 저장될 클립.  Clip recording source will be saved.

    const int HEADER_SIZE = 44;

    private string foldername;
    private string filename;

    private void Start()
    {
        // wav 파일은 '날짜-시간'의 이름을 가진 폴더 내에 저장됩니다.
        // The result wave file will be saved in folder named 'date-time'.
        foldername = DateTime.Now.ToString("yyyy-MM-dd-HH\\hmm\\m");
    }

    void FixedUpdate()
    {
        /*
        // 볼륨 확인용.  Check volume.
        if (Microphone.IsRecording(null))
        {
            int startPosition = Microphone.GetPosition(null) - 64;

            if (startPosition < 0)
                return;

            float[] waveData = new float[64];
            recordingClip.GetData(waveData, startPosition);

            float totalLoudness = 0;
            for (int i = 0; i < 64; i++)
                totalLoudness += Mathf.Abs(waveData[i]);

            if (totalLoudness < 0.1f)
                totalLoudness = 0;
            Debug.Log((totalLoudness).ToString());
        }
        */
    }

    // 녹음 시작. Start recording.
    public void StartRecording(string filename)
    {
        recordingClip = Microphone.Start(null, true, 10, 44100);
        this.filename = filename.EndsWith(".wav") ? filename : filename + ".wav";
    }

    // 녹음 종료. Stop recording.
    public void StopRecording()
    {
        if (Microphone.IsRecording(null))
        {
            int lastTime = Microphone.GetPosition(null);

            if (lastTime == 0)
                return;
            else
            {
                Microphone.End(Microphone.devices[0]);

                float[] samples = new float[recordingClip.samples];
                recordingClip.GetData(samples, 0);

                float[] cutSamples = new float[lastTime];
                Array.Copy(samples, cutSamples, cutSamples.Length - 1);

                recordingClip = AudioClip.Create("Notice", cutSamples.Length, 1, 44100, false);
                recordingClip.SetData(cutSamples, 0);

                Save();
            }
        }
    }

    // wav 파일로 녹음 저장.
    // Save recording file into wave file.
    private bool Save()
    {
        if (!filename.ToLower().EndsWith(".wav"))
        {
            filename += ".wav";
        }

        string path = Application.dataPath;
        path = Path.Combine(path.Substring(0, path.LastIndexOf('/')), "Recordings", foldername);

        var filepath = Path.Combine(path, filename);

        // Make sure directory exists if user is saving to sub dir.
        Directory.CreateDirectory(Path.GetDirectoryName(filepath));

        using (var fileStream = CreateEmpty(filepath))
        {

            ConvertAndWrite(fileStream, recordingClip);

            WriteHeader(fileStream, recordingClip);
        }

        return true; // TODO: return false if there's a failure saving the file
    }

    // 프로그램이 종료되는 경우 자동으로 파일을 저장함.
    // Save the wave file on program quit.
    private void OnApplicationQuit()
    {
        Save();
    }

    private AudioClip TrimSilence(AudioClip clip, float min)
    {
        var samples = new float[clip.samples];

        clip.GetData(samples, 0);

        return TrimSilence(new List<float>(samples), min, clip.channels, clip.frequency);
    }

    private AudioClip TrimSilence(List<float> samples, float min, int channels, int hz)
    {
        return TrimSilence(samples, min, channels, hz, false);
    }

    // _3D 파라미터는 Unity에서 deprecated (CS0618) — 제거하고 5인자 오버로드 사용.
    // 3D 공간 재생이 필요하면 AudioSource.spatialBlend로 제어할 것.
    private AudioClip TrimSilence(List<float> samples, float min, int channels, int hz, bool stream)
    {
        int i;

        for (i = 0; i < samples.Count; i++)
        {
            if (Mathf.Abs(samples[i]) > min)
            {
                break;
            }
        }

        samples.RemoveRange(0, i);

        for (i = samples.Count - 1; i > 0; i--)
        {
            if (Mathf.Abs(samples[i]) > min)
            {
                break;
            }
        }

        samples.RemoveRange(i, samples.Count - i);

        var clip = AudioClip.Create("TempClip", samples.Count, channels, hz, stream);

        clip.SetData(samples.ToArray(), 0);

        return clip;
    }

    private FileStream CreateEmpty(string filepath)
    {
        var fileStream = new FileStream(filepath, FileMode.Create);
        byte emptyByte = new byte();

        for (int i = 0; i < HEADER_SIZE; i++) //preparing the header
        {
            fileStream.WriteByte(emptyByte);
        }

        return fileStream;
    }

    private void ConvertAndWrite(FileStream fileStream, AudioClip clip)
    {
        var samples = new float[clip.samples];

        clip.GetData(samples, 0);

        Int16[] intData = new Int16[samples.Length];
        //converting in 2 float[] steps to Int16[], //then Int16[] to Byte[]

        Byte[] bytesData = new Byte[samples.Length * 2];
        //bytesData array is twice the size of
        //dataSource array because a float converted in Int16 is 2 bytes.

        int rescaleFactor = 32767; //to convert float to Int16

        for (int i = 0; i < samples.Length; i++)
        {
            intData[i] = (short)(samples[i] * rescaleFactor);
            Byte[] byteArr = new Byte[2];
            byteArr = BitConverter.GetBytes(intData[i]);
            byteArr.CopyTo(bytesData, i * 2);
        }

        fileStream.Write(bytesData, 0, bytesData.Length);
    }

    private void WriteHeader(FileStream fileStream, AudioClip clip)
    {

        var hz = clip.frequency;
        var channels = clip.channels;
        var samples = clip.samples;

        fileStream.Seek(0, SeekOrigin.Begin);

        Byte[] riff = System.Text.Encoding.UTF8.GetBytes("RIFF");
        fileStream.Write(riff, 0, 4);

        Byte[] chunkSize = BitConverter.GetBytes(fileStream.Length - 8);
        fileStream.Write(chunkSize, 0, 4);

        Byte[] wave = System.Text.Encoding.UTF8.GetBytes("WAVE");
        fileStream.Write(wave, 0, 4);

        Byte[] fmt = System.Text.Encoding.UTF8.GetBytes("fmt ");
        fileStream.Write(fmt, 0, 4);

        Byte[] subChunk1 = BitConverter.GetBytes(16);
        fileStream.Write(subChunk1, 0, 4);


        UInt16 one = 1;

        Byte[] audioFormat = BitConverter.GetBytes(one);
        fileStream.Write(audioFormat, 0, 2);

        Byte[] numChannels = BitConverter.GetBytes(channels);
        fileStream.Write(numChannels, 0, 2);

        Byte[] sampleRate = BitConverter.GetBytes(hz);
        fileStream.Write(sampleRate, 0, 4);

        Byte[] byteRate = BitConverter.GetBytes(hz * channels * 2); // sampleRate * bytesPerSample*number of channels, here 44100*2*2
        fileStream.Write(byteRate, 0, 4);

        UInt16 blockAlign = (ushort)(channels * 2);
        fileStream.Write(BitConverter.GetBytes(blockAlign), 0, 2);

        UInt16 bps = 16;
        Byte[] bitsPerSample = BitConverter.GetBytes(bps);
        fileStream.Write(bitsPerSample, 0, 2);

        Byte[] datastring = System.Text.Encoding.UTF8.GetBytes("data");
        fileStream.Write(datastring, 0, 4);

        Byte[] subChunk2 = BitConverter.GetBytes(samples * channels * 2);
        fileStream.Write(subChunk2, 0, 4);

        //		fileStream.Close();
    }
}
