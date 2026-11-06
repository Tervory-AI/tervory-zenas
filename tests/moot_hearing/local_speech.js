// The browser speech boundary is fake; the hearing, API, PostgreSQL and React UI are real.
window.spoken=[];window.recognitionStarts=0;window.recognitionAborts=0;
class Recognition {
  constructor(){if(window.speechMode!=='unsupported')this.processLocally=false;this.onresult=null;this.onend=null;this.onerror=null;}
  static async available(){if(window.speechMode==='pending')return new Promise(()=>{});return window.speechMode==='unavailable'?'unavailable':'available';}
  start(){if(this.processLocally!==true)throw Error('remote speech forbidden');window.recognitionStarts++;window.activeRecognition=this;if(window.speechMode==='denied')queueMicrotask(()=>this.onerror?.());}
  stop(){queueMicrotask(()=>this.onend?.());}
  abort(){window.recognitionAborts++;queueMicrotask(()=>this.onend?.());}
}
window.SpeechRecognition=Recognition;
window.emitVoice=(text,final=true)=>{const result=[{transcript:text}];result.isFinal=final;
 window.activeRecognition.onresult({resultIndex:0,results:[result]});};
window.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};
Object.defineProperty(window,'speechSynthesis',{value:{
 getVoices:()=>window.speechMode==='remote-tts'?[{lang:'en-US',localService:false}]:[{lang:'en-US',localService:true}],
 cancel(){},speak(utterance){if(window.speechMode==='tts-error')throw Error('local playback failed');if(!utterance.voice?.localService)throw Error('remote voice forbidden');
 window.spoken.push(utterance.text);queueMicrotask(()=>utterance.onend?.());}
}});
