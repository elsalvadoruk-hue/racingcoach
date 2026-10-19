/* Audio stays in memory until an explicit push-to-talk recording is submitted. */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor('voice-capture', VoiceCaptureProcessor);
