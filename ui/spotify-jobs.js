(function () {
  "use strict";
  const parse = value => typeof value === "string" ? JSON.parse(value) : value || {};
  const call = async (method, params) => parse(await window.astra.callBackend(method, params));
  window.astraSpotifyJob = async (operation, params = {}) => {
    const started = await call("music_spotify_job_start", {...params, operation});
    if (started.error) throw new Error(started.error);
    const deadline = Date.now() + 110000;
    while (Date.now() < deadline) {
      const job = await call("music_spotify_job_status", {job_id: started.job_id});
      if (job.error) throw new Error(job.error);
      if (job.status === "done") return job.result;
      await new Promise(resolve => setTimeout(resolve, 400));
    }
    throw new Error("Spotify долго не отвечает. Проверьте вход и повторите действие.");
  };
})();
