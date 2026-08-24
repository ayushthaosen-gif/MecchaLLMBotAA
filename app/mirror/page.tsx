"use client";
import {useEffect,useRef,useState} from "react";
import {DrawingUtils,FaceLandmarker,FilesetResolver,PoseLandmarker} from "@mediapipe/tasks-vision";

type Joints={right_shoulder:number;right_elbow:number;left_shoulder:number;left_elbow:number};
type Pos={x:number;y:number};
type JointPositions={right_shoulder:Pos;right_elbow:Pos;left_shoulder:Pos;left_elbow:Pos};
type LockState="searching"|"locking"|"tracking";
type LogLine={ts:string;kind:string;msg:string};
type Expression="neutral"|"happy"|"sad"|"angry"|"surprised"|"fear"|"disgust";
const clamp=(v:number,a=15,b=165)=>Math.max(a,Math.min(b,v));
function angle(a:{x:number;y:number},b:{x:number;y:number},c:{x:number;y:number}){
 const u={x:a.x-b.x,y:a.y-b.y},v={x:c.x-b.x,y:c.y-b.y};
 const d=Math.hypot(u.x,u.y)*Math.hypot(v.x,v.y);return d?Math.acos(clamp((u.x*v.x+u.y*v.y)/d,-1,1))*180/Math.PI:90;
}

// Shoulder elevation is measured relative to a torso reference point. The
// real hip landmark is the best reference when actually visible — but a
// typical webcam framing (sitting at a desk) usually crops the hips out
// entirely, and MediaPipe still reports a low-confidence "guess" there.
// Rather than requiring hips at all, derive a virtual torso reference
// from the head/shoulders — landmarks that are essentially always in
// frame together — by extending the nose-to-shoulder-midpoint vector
// further down. A rough proxy for "where the hips are" based on typical
// body proportions, not a measurement, but it keeps tracking working for
// any normal upper-body framing instead of only full-body shots.
function torsoReference(l:any[]){
 const nose=l[0], mid={x:(l[11].x+l[12].x)/2,y:(l[11].y+l[12].y)/2};
 const dx=mid.x-nose.x, dy=mid.y-nose.y;
 return {x:mid.x+dx*2,y:mid.y+dy*2};
}
function side(l:any[],shoulder:number,elbow:number,wrist:number,hip:number,virtualHip:{x:number;y:number}){
 const hipPoint=(l[hip].visibility??1)>.3?l[hip]:virtualHip;
 const elevation=angle(hipPoint,l[shoulder],l[elbow]);
 return {shoulder:clamp(30+elevation*2/3),elbow:clamp(angle(l[shoulder],l[elbow],l[wrist]))};
}

// Crossed-wrists "follow mode" toggle: each wrist has moved to the
// OPPOSITE side of the body midline from its own shoulder. Robust to
// mirroring since it only compares each side's own wrist/shoulder pair,
// never an absolute left/right in screen space.
function wristsCrossed(l:any[]){
 const midX=(l[11].x+l[12].x)/2;
 const leftCrossed=(l[15].x-midX)*(l[11].x-midX)<0;
 const rightCrossed=(l[16].x-midX)*(l[12].x-midX)<0;
 return leftCrossed&&rightCrossed;
}

// Palette designed for 7 distinct, immediately-readable hues, each with an
// established color/emotion association rather than an arbitrary pick.
// UI-only hex values — the real hardware command sent to the robot is
// just the mood NAME (see eyes.py's MOOD_COLORS for the actual 0-7-per-
// channel LED intensities); the two color systems are matched in spirit,
// not pixel-for-pixel, since they're optimized for different media.
const EXPRESSION_COLORS:Record<Expression,string>={
 neutral:"#4fd8e0", happy:"#ffd23f", sad:"#3d6fd6", angry:"#ef4444",
 surprised:"#c65fff", fear:"#9b6dff", disgust:"#7cc242",
};

// MediaPipe returns 52 independent blendshape coefficients rather than a
// ready-made emotion label. Combine multiple anatomically related signals,
// smooth them over time, and require a short stable run before changing the
// robot's eyes. This avoids the single-feature false positives and flicker
// caused by classifying every frame independently.
const FACE_INFERENCE_HZ=20;
const FACE_FRAME_INTERVAL_MS=1000/FACE_INFERENCE_HZ;
const FACE_LOST_GRACE_MS=450;
const BLENDSHAPE_EMA_ALPHA=.28;
const EXPRESSION_ENTER_THRESHOLD=.36;
const EXPRESSION_SWITCH_MARGIN=.08;

type Blendshape={categoryName:string;score:number};
type ExpressionSnapshot={expression:Expression;confidence:number;faceTracked:boolean;changed:boolean};
type ExpressionScoreSet={scores:Record<Expression,number>;winner:Expression};

const clamp01=(v:number)=>Math.max(0,Math.min(1,v));
const mean=(...values:number[])=>values.reduce((sum,value)=>sum+value,0)/values.length;

function scoreExpressions(values:Map<string,number>):ExpressionScoreSet{
 const get=(name:string)=>values.get(name)||0;
 const bilateral=(name:string)=>mean(get(name+"Left"),get(name+"Right"));
 const smile=bilateral("mouthSmile"),frown=bilateral("mouthFrown");
 const cheekSquint=bilateral("cheekSquint"),dimple=bilateral("mouthDimple");
 const browDown=bilateral("browDown"),browOuterUp=bilateral("browOuterUp");
 const eyeWide=bilateral("eyeWide"),eyeSquint=bilateral("eyeSquint");
 const noseSneer=bilateral("noseSneer"),upperLip=bilateral("mouthUpperUp");
 const mouthPress=bilateral("mouthPress"),mouthStretch=bilateral("mouthStretch");
 const mouthLowerDown=bilateral("mouthLowerDown");
 const jawOpen=get("jawOpen"),browInnerUp=get("browInnerUp");
 const mouthFunnel=get("mouthFunnel"),mouthShrugUpper=get("mouthShrugUpper");

 const scores:Record<Expression,number>={
  neutral:0,
  happy:clamp01(smile*.60+cheekSquint*.25+dimple*.15-frown*.22),
  surprised:clamp01(jawOpen*.34+eyeWide*.32+Math.max(browInnerUp,browOuterUp)*.22+mouthFunnel*.12-smile*.18),
  fear:clamp01(eyeWide*.32+browInnerUp*.26+mouthStretch*.26+frown*.16-jawOpen*.14-smile*.18),
  angry:clamp01(browDown*.42+eyeSquint*.23+mouthPress*.20+noseSneer*.15-smile*.24),
  sad:clamp01(frown*.48+browInnerUp*.28+mouthLowerDown*.14+eyeSquint*.10-smile*.20),
  disgust:clamp01(noseSneer*.50+upperLip*.30+mouthShrugUpper*.20-smile*.15),
 };
 const ranked=(Object.entries(scores) as [Expression,number][])
  .filter(([name])=>name!=="neutral").sort((a,b)=>b[1]-a[1]);
 const [best,bestScore]=ranked[0];
 scores.neutral=clamp01(1-bestScore);
 return {scores,winner:bestScore>=EXPRESSION_ENTER_THRESHOLD?best:"neutral"};
}

class StableExpressionTracker{
 private ema=new Map<string,number>();
 private candidate:Expression="neutral";
 private candidateFrames=0;
 private lastFaceSeen=0;
 current:Expression="neutral";
 confidence=0;
 faceTracked=false;

 reset(){
  this.ema.clear();this.candidate="neutral";this.candidateFrames=0;
  this.lastFaceSeen=0;this.current="neutral";this.confidence=0;this.faceTracked=false;
 }
 snapshot():ExpressionSnapshot{
  return {expression:this.current,confidence:this.confidence,faceTracked:this.faceTracked,changed:false};
 }
 update(blendshapes:Blendshape[]|undefined,now:number):ExpressionSnapshot{
  const previous=this.current;
  if(!blendshapes?.length){
   if(now-this.lastFaceSeen<=FACE_LOST_GRACE_MS)return this.snapshot();
   this.ema.clear();this.candidate="neutral";this.candidateFrames=0;
   this.current="neutral";this.confidence=0;this.faceTracked=false;
   return {expression:this.current,confidence:0,faceTracked:false,changed:previous!==this.current};
  }

  this.lastFaceSeen=now;this.faceTracked=true;
  for(const shape of blendshapes){
   const prior=this.ema.get(shape.categoryName);
   this.ema.set(shape.categoryName,prior===undefined?shape.score:prior+(shape.score-prior)*BLENDSHAPE_EMA_ALPHA);
  }
  const result=scoreExpressions(this.ema);
  let requested=result.winner;
  if(this.current!=="neutral"){
   const currentScore=result.scores[this.current];
   if(requested==="neutral"&&currentScore>=.26)requested=this.current;
   else if(requested!==this.current&&requested!=="neutral"&&result.scores[requested]<currentScore+EXPRESSION_SWITCH_MARGIN)requested=this.current;
  }

  if(requested===this.current){
   this.candidate=requested;this.candidateFrames=0;
  }else{
   if(requested===this.candidate)this.candidateFrames+=1;
   else{this.candidate=requested;this.candidateFrames=1}
   const requiredFrames=requested==="neutral"?4:3;
   if(this.candidateFrames>=requiredFrames){
    this.current=requested;this.candidateFrames=0;
   }
  }
  this.confidence=this.current==="neutral"?result.scores.neutral:result.scores[this.current];
  return {expression:this.current,confidence:this.confidence,faceTracked:true,changed:previous!==this.current};
 }
}
// Small glowing dot + crosshair at a tracked joint — drawn on the same
// (mirrored) canvas as the wireframe, so it stays pixel-locked to the
// joint. Angle text is NOT drawn here — canvas text would render
// mirror-flipped/backwards along with the video (see .camera's
// transform:scaleX(-1)); the numeric labels are separate, non-mirrored
// DOM chips positioned over the video instead (see JointChip below).
function drawJointMarker(ctx:CanvasRenderingContext2D,x:number,y:number,confident:boolean){
 ctx.save();
 ctx.shadowColor=confident?"#4fd8e0":"#c9a8ff90";ctx.shadowBlur=confident?14:8;
 ctx.strokeStyle=confident?"#4fd8e0":"#c9a8ffb0";ctx.lineWidth=1.5;
 ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.stroke();
 ctx.beginPath();ctx.moveTo(x-14,y);ctx.lineTo(x-5,y);ctx.moveTo(x+5,y);ctx.lineTo(x+14,y);
 ctx.moveTo(x,y-14);ctx.lineTo(x,y-5);ctx.moveTo(x,y+5);ctx.lineTo(x,y+14);ctx.stroke();
 ctx.restore();
}

function JointChip({label,pos,angleDeg}:{label:string;pos:Pos;angleDeg:number}){
 // pos.x/.y are normalized [0,1] in the RAW (unmirrored) camera frame.
 // This chip is a sibling of the mirrored video/canvas, not a mirrored
 // element itself, so its own position must be flipped by hand
 // (1 - x) to land on the correct spot in the mirrored display.
 return <div className="jointChip" style={{left:`${(1-pos.x)*100}%`,top:`${pos.y*100}%`}}>
  <span className="jointChipLabel">{label}</span><span className="jointChipAngle">{Math.round(angleDeg)}°</span>
 </div>;
}

export default function MirrorPage(){
 const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null);
 const tracker=useRef<PoseLandmarker|null>(null),faceTracker=useRef<FaceLandmarker|null>(null);
 const raf=useRef(0),lastSent=useRef(0),sending=useRef(false),smooth=useRef<Joints|null>(null);
 const fpsRef=useRef(0),lastFrameRef=useRef(performance.now()),lastHudRef=useRef(0);
 const lockRef=useRef<LockState>("searching"),uplinkRef=useRef(false);
 const expressionRef=useRef<Expression>("neutral");
 const expressionTrackerRef=useRef(new StableExpressionTracker());
 const cachedFaceResultRef=useRef<any>(null),lastFaceProcessedRef=useRef(0);

 // Follow mode: crossing wrists toggles it on/off (debounced so a held
 // cross doesn't flap). While on, the shoulder-width in frame at the
 // moment of toggling becomes the baseline "hold this distance" target.
 const followRef=useRef(false),followBaselineRef=useRef<number|null>(null);
 const lastCrossedRef=useRef(false),lastToggleRef=useRef(0),lastLoggedLocoRef=useRef<string|null>(null);
 const FOLLOW_TOGGLE_DEBOUNCE_MS=1200,FOLLOW_DEAD_BAND=0.08;

 // Visual/detection framerate cap: the backend only accepts arm-angle
 // updates at 20Hz (MirrorController.MIN_INTERVAL_S) — anything computed
 // faster than roughly 2x that is wasted battery/CPU with no benefit,
 // since neither the eye nor the robot can use it. requestAnimationFrame
 // still runs at the display's native rate for smooth scheduling; the
 // expensive detection+draw work inside is throttled to this interval.
 const BOT_MAX_HZ=20, VISUAL_HZ_CAP=BOT_MAX_HZ*2, VISUAL_FRAME_INTERVAL_MS=1000/VISUAL_HZ_CAP;
 const lastProcessedRef=useRef(0);

 const [running,setRunning]=useState(false),[status,setStatus]=useState("Camera stopped"),[endpoint,setEndpoint]=useState(""),[token,setToken]=useState("");
 const [hud,setHud]=useState({fps:0,latencyMs:0,lock:"searching" as LockState,streaming:false,expression:"neutral" as Expression,expressionConfidence:0,faceTracked:false,follow:false,followAction:"stop"});
 const [joints,setJoints]=useState<Joints|null>(null);
 const [positions,setPositions]=useState<JointPositions|null>(null);
 const [log,setLog]=useState<LogLine[]>([{ts:"--:--:--",kind:"sys",msg:"idle"}]);
 function pushLog(kind:string,msg:string){
  const ts=new Date().toLocaleTimeString([],{hour12:false});
  setLog(v=>[...v.slice(-59),{ts,kind,msg}]);
 }
 useEffect(()=>{const chat=localStorage.getItem("meccanoid.endpoint")||"";setEndpoint(localStorage.getItem("meccanoid.mirrorEndpoint")||chat.replace(/\/chat\/?$/,"/mirror_pose"));setToken(localStorage.getItem("meccanoid.token")||"");return()=>{stop(false);tracker.current?.close();faceTracker.current?.close()}},[]);
 function stop(report=true){
  cancelAnimationFrame(raf.current);
  video.current?.srcObject instanceof MediaStream&&video.current.srcObject.getTracks().forEach(t=>t.stop());
  setRunning(false);
  setHud(h=>({...h,lock:"searching",streaming:false,expression:"neutral",expressionConfidence:0,faceTracked:false,follow:false}));
  expressionTrackerRef.current.reset();expressionRef.current="neutral";cachedFaceResultRef.current=null;lastFaceProcessedRef.current=0;
  if(followRef.current)pushLog("track","follow mode released (camera stopped)");
  followRef.current=false;followBaselineRef.current=null;
  if(report){setStatus("Camera stopped — robot auto-rests within 750 ms.");pushLog("sys","camera stopped — robot auto-rests within 750ms")}
 }
 async function start(){
  try{
   setStatus("Loading on-device pose model…");pushLog("sys","initializing pose + face engines");pushLog("net","fetching WASM runtime...");
   const vision=await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm");
   pushLog("net","WASM runtime ready");
   if(!tracker.current){
    pushLog("net","loading pose_landmarker_lite model...");
    tracker.current=await PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",delegate:"GPU"},runningMode:"VIDEO",numPoses:1,minPoseDetectionConfidence:.6,minTrackingConfidence:.6});
    pushLog("net","pose model ready");
   }
   if(!faceTracker.current){
    pushLog("net","loading face_landmarker model (expression tracking)...");
    faceTracker.current=await FaceLandmarker.createFromOptions(vision,{
     baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",delegate:"GPU"},
     runningMode:"VIDEO",
     // MediaPipe applies built-in landmark smoothing only in single-face mode.
     numFaces:1,minFaceDetectionConfidence:.6,minFacePresenceConfidence:.6,minTrackingConfidence:.6,
     outputFaceBlendshapes:true,
    });
    pushLog("net","face model ready");
   }
   setStatus("Requesting camera access…");pushLog("cam","requesting camera access...");
   const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:{ideal:960},height:{ideal:720}},audio:false});
   const settings=stream.getVideoTracks()[0]?.getSettings()||{};
   pushLog("cam",`stream acquired ${settings.width||"?"}x${settings.height||"?"}`);
   if(!video.current)return;video.current.srcObject=stream;await video.current.play();
   setRunning(true);setStatus("Tracking locally");lastFrameRef.current=performance.now();lastProcessedRef.current=0;
   lockRef.current="searching";uplinkRef.current=false;
   expressionTrackerRef.current.reset();expressionRef.current="neutral";cachedFaceResultRef.current=null;lastFaceProcessedRef.current=0;
   followRef.current=false;followBaselineRef.current=null;lastCrossedRef.current=false;
   pushLog("sys",`tracking loop started (visuals capped ${VISUAL_HZ_CAP}Hz — 2x the robot's own ${BOT_MAX_HZ}Hz limit)`);
   loop();
  }catch(e){
   const msg=e instanceof Error?e.message:"Unable to start camera";
   pushLog("err",msg);stop(false);setStatus(msg);
  }
 }
 function loop(){
  const v=video.current,c=canvas.current,t=tracker.current,ft=faceTracker.current;
  if(!v||!c||!t||!ft||v.readyState<2){raf.current=requestAnimationFrame(loop);return}

  const now=performance.now();
  if(now-lastProcessedRef.current<VISUAL_FRAME_INTERVAL_MS){raf.current=requestAnimationFrame(loop);return}
  lastProcessedRef.current=now;

  c.width=v.videoWidth;c.height=v.videoHeight;const ctx=c.getContext("2d")!;ctx.clearRect(0,0,c.width,c.height);

  const dt=now-lastFrameRef.current;lastFrameRef.current=now;
  if(dt>0)fpsRef.current=fpsRef.current?fpsRef.current*.9+(1000/dt)*.1:1000/dt;

  const detectStart=performance.now();
  const result=t.detectForVideo(v,now);
  let faceUpdated=false;
  if(now-lastFaceProcessedRef.current>=FACE_FRAME_INTERVAL_MS){
   cachedFaceResultRef.current=ft.detectForVideo(v,now);
   lastFaceProcessedRef.current=now;faceUpdated=true;
  }
  const faceResult=cachedFaceResultRef.current;
  const latencyMs=performance.now()-detectStart;
  // Two separate gates, not one: whether to DRAW (loose — any pose at
  // all) vs whether it's confident enough to COMPUTE + STREAM angles to
  // a real robot (stricter — arm joints specifically). Requiring all 8
  // of shoulders/elbows/wrists/HIPS visible before drawing anything meant
  // a typical webcam framing (sitting at a desk, hips out of frame) never
  // rendered a wireframe at all, even though the arms were being tracked
  // fine. Hips are only needed for the elevation formula, not to decide
  // whether to show anything.
  const poseDetected=!!result.landmarks[0];
  const armsConfident=poseDetected&&[11,12,13,14,15,16].every(i=>(result.landmarks[0][i].visibility??1)>.45);
  let liveJoints:Joints|null=null,livePositions:JointPositions|null=null;
  let lock:LockState="searching";
  let locomotionAction:string|null=null;

  const blendshapes=faceResult?.faceBlendshapes?.[0]?.categories as Blendshape[]|undefined;
  const faceState=faceUpdated?expressionTrackerRef.current.update(blendshapes,now):expressionTrackerRef.current.snapshot();
  expressionRef.current=faceState.expression;
  if(faceState.changed)pushLog("track","expression: "+faceState.expression+" ("+Math.round(faceState.confidence*100)+"%)");
  const faceLandmarks=faceResult?.faceLandmarks?.[0];
  if(faceLandmarks&&faceState.faceTracked){
   const faceColor=EXPRESSION_COLORS[faceState.expression];
   const faceDrawing=new DrawingUtils(ctx);
   faceDrawing.drawConnectors(faceLandmarks,FaceLandmarker.FACE_LANDMARKS_CONTOURS,{color:faceColor+"aa",lineWidth:1.2});
   faceDrawing.drawConnectors(faceLandmarks,FaceLandmarker.FACE_LANDMARKS_LEFT_IRIS,{color:"#ffffffcc",lineWidth:1});
   faceDrawing.drawConnectors(faceLandmarks,FaceLandmarker.FACE_LANDMARKS_RIGHT_IRIS,{color:"#ffffffcc",lineWidth:1});
  }
  if(poseDetected){
   const l=result.landmarks[0];
   new DrawingUtils(ctx).drawConnectors(l,PoseLandmarker.POSE_CONNECTIONS,{color:armsConfident?"#4fd8e0aa":"#4fd8e055",lineWidth:2});
   // Front-camera mirror: the person's left arm drives the robot's right arm.
   const vHip=torsoReference(l);
   const right=side(l,11,13,15,23,vHip),left=side(l,12,14,16,24,vHip);
   const raw:Joints={right_shoulder:right.shoulder,right_elbow:right.elbow,left_shoulder:left.shoulder,left_elbow:left.elbow};
   const prev=smooth.current||raw;const smoothed=Object.fromEntries(Object.entries(raw).map(([k,x])=>[k,Math.round((prev as any)[k]*.7+x*.3)])) as Joints;
   smooth.current=smoothed;liveJoints=smoothed;
   livePositions={right_shoulder:{x:l[11].x,y:l[11].y},right_elbow:{x:l[13].x,y:l[13].y},left_shoulder:{x:l[12].x,y:l[12].y},left_elbow:{x:l[14].x,y:l[14].y}};
   for(const p of Object.values(livePositions))drawJointMarker(ctx,p.x*c.width,p.y*c.height,armsConfident);

   lock=armsConfident?"tracking":"locking";
   if(lock!==lockRef.current){
    if(lock==="tracking")pushLog("track","arm tracking lock confirmed");
    else if(lockRef.current==="searching")pushLog("track","pose acquired, locking arm joints...");
   }

   // Crossed-wrists follow-mode toggle, edge-triggered with a debounce so
   // holding the pose doesn't flap it on/off repeatedly.
   const crossedNow=armsConfident&&wristsCrossed(l);
   if(crossedNow&&!lastCrossedRef.current&&now-lastToggleRef.current>FOLLOW_TOGGLE_DEBOUNCE_MS){
    lastToggleRef.current=now;
    followRef.current=!followRef.current;
    if(followRef.current){
     followBaselineRef.current=Math.abs(l[12].x-l[11].x)*c.width;
     pushLog("track",`follow mode ON — baseline shoulder width ${followBaselineRef.current.toFixed(0)}px`);
    }else{
     followBaselineRef.current=null;
     pushLog("track","follow mode OFF");
    }
   }
   lastCrossedRef.current=crossedNow;

   if(followRef.current&&followBaselineRef.current){
    const currentWidth=Math.abs(l[12].x-l[11].x)*c.width;
    const ratio=currentWidth/followBaselineRef.current;
    // Crossing your wrists LOCKS the distance at that moment as the
    // target. From then on: smaller in frame than the locked width means
    // you've stepped back, so the robot drives forward to close the gap
    // until it's back at the locked distance, then stops. Deliberately
    // one-directional for now — no backward/retreat if you step closer
    // than the locked distance, only "catch up if you back away."
    const wanted=ratio<1-FOLLOW_DEAD_BAND?"forward":"stop";
    locomotionAction=wanted;
    if(wanted!==lastLoggedLocoRef.current){
     pushLog("track",`follow: ${wanted} (width ${currentWidth.toFixed(0)}px vs baseline ${followBaselineRef.current.toFixed(0)}px)`);
     lastLoggedLocoRef.current=wanted;
    }
   }else{
    lastLoggedLocoRef.current=null;
   }

   if(armsConfident&&endpoint&&now-lastSent.current>100&&!sending.current){
    lastSent.current=now;sending.current=true;
    if(!uplinkRef.current)pushLog("uplink",`connecting to ${endpoint}`);
    const payload:any={joints:smoothed,mood:expressionRef.current};
    if(locomotionAction)payload.locomotion=locomotionAction;
    fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify(payload)})
     .then(r=>{if(!r.ok)throw new Error(`Mirror endpoint HTTP ${r.status}`);if(!uplinkRef.current){pushLog("uplink","first frame acknowledged");uplinkRef.current=true}})
     .catch(e=>{setStatus(e.message);pushLog("err",e.message);uplinkRef.current=false})
     .finally(()=>{sending.current=false});
   }
   setStatus(armsConfident?"Tracking locally":"Pose seen — move shoulders/elbows/wrists fully into frame");
  }else{
   setStatus("No pose detected — step into frame");
   if(lockRef.current!=="searching")pushLog("track","pose lost — searching");
   uplinkRef.current=false;
   lastLoggedLocoRef.current=null;
  }
  lockRef.current=lock;

  // "Streaming" reads straight off lastSent.current (a ref, always
  // current) rather than the hud state — loop() recurses via its own
  // requestAnimationFrame call, not through React re-renders, so a
  // captured `hud` value here would just be whatever it was on the
  // very first call and never update (a stale-closure trap).
  const streaming=!!endpoint&&armsConfident&&(now-lastSent.current<300);

  // React state (and therefore the DOM/HUD) is throttled to ~8/s —
  // plenty smooth for numbers changing under smoothed tracking, and
  // far cheaper than re-rendering on every processed frame.
  if(now-lastHudRef.current>120){
   lastHudRef.current=now;
   setHud({fps:Math.round(fpsRef.current),latencyMs:Math.round(latencyMs),lock,streaming,
     expression:expressionRef.current,expressionConfidence:faceState.confidence,faceTracked:faceState.faceTracked,follow:followRef.current,followAction:locomotionAction||"stop"});
   setJoints(liveJoints);
   setPositions(livePositions);
  }
  raf.current=requestAnimationFrame(loop);
 }
 function save(){localStorage.setItem("meccanoid.mirrorEndpoint",endpoint.trim());setStatus(endpoint?"Mirror endpoint saved":"Wireframe-only mode")}
 const chips:[string,keyof Joints][]=[["R-SHOULDER","right_shoulder"],["R-ELBOW","right_elbow"],["L-SHOULDER","left_shoulder"],["L-ELBOW","left_elbow"]];
 const lockLabel={searching:"SEARCHING",locking:"LOCKING",tracking:"TRACKING"}[hud.lock];
 const lockClass={searching:"warn",locking:"partial",tracking:"ok"}[hud.lock];
 const eyeColor=EXPRESSION_COLORS[hud.expression];
 return <main className="mirrorPage"><header><a href="/">← Console</a><div className="brand"><b>MECCANOID</b> // POSE MIRROR</div></header>
 <section className="panel"><h1>Copy my arm movements</h1><p className="note">Face contours, iris markers, and pose wireframes are processed on this device. Only four joint angles (plus a stabilized expression tag and an optional follow-mode direction) are transmitted; video never goes to the robot or cloud.</p>
  <div className="camera">
   <video ref={video} playsInline muted/><canvas ref={canvas}/>
   <div className="hudCorners"><i/><i/><i/><i/></div>
   <div className={`hudReadout wide${hud.lock!=="searching"?" live":""}`}>
    <div><small>FPS</small><b>{hud.fps || "—"}</b></div>
    <div><small>LATENCY</small><b>{hud.lock!=="searching"?`${hud.latencyMs}ms`:"—"}</b></div>
    <div><small>LOCK</small><b className={lockClass}>{lockLabel}</b></div>
    <div><small>UPLINK</small><b className={hud.streaming?"ok":"dim"}>{endpoint?(hud.streaming?"LIVE":(hud.lock==="tracking"?"IDLE":"WAIT")):"OFF"}</b></div>
    <div><small>FACE</small><b className={!hud.faceTracked?"warn":hud.expression==="neutral"?"dim":"ok"}>{hud.faceTracked?hud.expression.toUpperCase()+" "+Math.round(hud.expressionConfidence*100)+"%":"SEARCHING"}</b></div>
    <div><small>FOLLOW</small><b className={hud.follow?"ok":"dim"}>{hud.follow?`ON (${hud.followAction.toUpperCase()})`:"OFF"}</b></div>
   </div>
   <div className="eyePair"><span className="eyeDot" style={{background:eyeColor,boxShadow:`0 0 8px ${eyeColor}`}}/><span className="eyeDot" style={{background:eyeColor,boxShadow:`0 0 8px ${eyeColor}`}}/></div>
   {positions&&chips.map(([label,key])=><JointChip key={key} label={label} pos={positions[key]} angleDeg={joints?joints[key]:90}/>)}
   <span className="camStatus">{status}</span>
  </div>
  <div className="angleGrid">
   {chips.map(([label,key])=><div key={key}><small>{label}</small><b>{joints?`${joints[key]}°`:"—"}</b></div>)}
  </div>
  <p className="note" style={{marginTop:8}}>Cross your wrists in front of your chest to toggle <b>follow mode</b> — the robot locks your current apparent distance (shoulder width in frame) and drives forward to close the gap if you step back, stopping once it's caught up. No backward/retreat behavior yet if you step closer. Cross again to release.</p>
  <div className="mirrorButtons"><button onClick={()=>running?stop():start()}>{running?"STOP MIRROR":"START CAMERA"}</button></div>
  <div className="terminal">
   {log.map((l,i)=><div key={i} className="tline"><span className="t-ts">[{l.ts}]</span> <span className={`t-${l.kind}`}>{l.kind.toUpperCase()}</span> :: {l.msg}</div>)}
   <div className="tline"><span className="term-cursor"/></div>
  </div>
 </section>
 <section className="panel"><h2>Robot connection</h2><label>HTTPS mirror endpoint<input value={endpoint} onChange={e=>setEndpoint(e.target.value)} placeholder="https://your-robot.example.com/mirror_pose"/></label><label>Bearer token<input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Same DASHBOARD_TOKEN as the Pi"/></label><button className="outline" onClick={save}>SAVE MIRROR SETTINGS</button><p className="note">On the Pi set ENABLE_MIRROR_CONTROL=1, DASHBOARD_TOKEN, and MIRROR_ALLOWED_ORIGIN. A public HTTPS page cannot call an insecure HTTP robot endpoint; use HTTPS or run the dashboard locally.</p></section>
 </main>;
}
