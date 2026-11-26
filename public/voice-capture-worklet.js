/* Audio stays in memory until an explicit push-to-talk recording is submitted. */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.recording = false;
    this.port.onmessage = ({ data }) => {
      this.recording = Boolean(data.recording);
      // Messages from this port retain order: every already captured buffer
      // arrives before this acknowledgement.
      if (data.type === 'finish') this.port.postMessage({ type: 'stopped', id: data.id });
    };
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (this.recording && channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor('voice-capture', VoiceCaptureProcessor);
