"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeAudios, ownTracks } = require("../ui/easyvk/audio-identity.cjs");

function row(id, owner, url = "") {
  const result = new Array(14).fill("");
  result[0] = id; result[1] = owner; result[2] = url;
  result[3] = `Track ${id}`; result[13] = "a/b/action/d/e/hash";
  return result;
}

test("reload URLs follow owner and track identity even when returned out of order", async () => {
  const rows = [row(1, 9), row(2, 9), row(1, 10)];
  const api = {
    getById: async () => [row(1, 10, "third"), row(2, 9, "second"), row(1, 9, "first")],
    getAudioAsObject: r => ({ id: r[0], owner_id: r[1], url: r[2] }),
  };
  const tracks = await normalizeAudios.call(api, rows);
  assert.deepEqual(tracks.map(track => track.url), ["first", "second", "third"]);
});

test("a missing reload result never borrows another track's URL", async () => {
  const api = { getById: async () => [row(1, 9, "first")], getAudioAsObject: r => ({ url: r[2] }) };
  const tracks = await normalizeAudios.call(api, [row(1, 9), row(2, 9)]);
  assert.equal(tracks[1].url, "");
});

test("personal library pagination supports audios and list pages in VK order", async () => {
  const calls = [];
  const api = { audio: { get: async params => {
    calls.push(params);
    return params.more ? { list: [{ id: 2, owner_id: 9 }, { id: 3, owner_id: 9 }] }
      : { audios: [{ id: 1, owner_id: 9 }, { id: 2, owner_id: 9 }], more: { next_from: "2" } };
  } } };
  assert.deepEqual((await ownTracks(api, 9)).map(track => track.id), [1, 2, 3]);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].raw, true);
});

test("repeated pagination cursor reports failure instead of looping forever", async () => {
  const api = { audio: { get: async () => ({ audios: [], more: { next_from: "2" } }) } };
  await assert.rejects(ownTracks(api, 9), /повторил страницу/);
});
