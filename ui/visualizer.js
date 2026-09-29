const aurora = document.querySelector(".aurora");
const rings = [...document.querySelectorAll(".rings span")];
let audioData = null;
let unsubscribe = () => {};
function requestAudio() {
  if (typeof window.astra?.requestAudioData !== "function") return false;
  unsubscribe = window.astra.requestAudioData((data) => { audioData = data; });
  return true;
}

// The bridge is injected after this script may already run, so subscribing once
// at load time can silently leave the visualizer frozen at its resting state.
if (!requestAudio()) {
  let waited = 0;
  const timer = setInterval(() => {
    if (requestAudio() || (waited += 100) > 5000) clearInterval(timer);
  }, 100);
}

function frame() {
  const bands = audioData?.bands || audioData?.data || [];
  const energy = bands.reduce((sum, value) => sum + Math.max(0, Number(value) || 0), 0) / Math.max(1, bands.length);
  const pulse = Math.min(1, energy / 100);
  aurora.style.opacity = String(.18 + pulse * .65);
  aurora.style.transform = `translate3d(${Math.sin(Date.now() / 900) * 2}px, ${Math.cos(Date.now() / 1100) * 2}px, 0) scale(${1 + pulse * .04})`;
  rings.forEach((ring, index) => {
    const value = bands[Math.min(bands.length - 1, Math.floor(index * bands.length / rings.length))] || 0;
    const amount = Math.min(1, Number(value) / 100);
    ring.style.opacity = String(.06 + amount * .4);
    ring.style.transform = `scale(${.45 + amount * (.55 + index * .08)})`;
  });
  requestAnimationFrame(frame);
}

requestAudio();
requestAnimationFrame(frame);
window.addEventListener("beforeunload", () => unsubscribe());
