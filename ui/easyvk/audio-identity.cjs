"use strict";

function identity(row) {
  return Array.isArray(row) ? `${row[1]}_${row[0]}` : `${row.owner_id}_${row.id}`;
}

// EasyVK compares undefined array.full_id values and reuses the first URL.
// Always join reload_audio rows by their actual owner/track pair.
async function normalizeAudios(rows, params = {}) {
  if (params.raw) return rows.map(row => this.getAudioAsObject(row));
  const ids = rows.map(row => {
    const hashes = String(row[13] || "").split("/");
    return hashes[2] && hashes[5] ? `${identity(row)}_${hashes[2]}_${hashes[5]}` : null;
  }).filter(Boolean);
  const fetched = ids.length ? await this.getById({ ids: ids.join(",") }) : [];
  const byId = new Map(fetched.map(row => [identity(row), row]));
  return rows.map(row => {
    const fresh = byId.get(identity(row));
    // Use the fresh URL explicitly; retain the requested track's metadata.
    const merged = fresh ? row.map((value, index) => index === 2 ? fresh[2] : value || fresh[index]) : row;
    return this.getAudioAsObject(merged);
  });
}

async function ownTracks(api, ownerId) {
  let page = await api.audio.get({ owner_id: ownerId, raw: true });
  const tracks = new Map();
  const cursors = new Set();
  while (true) {
    const rows = page.audios || page.list || [];
    for (const row of rows) {
      if (row) tracks.set(identity(row), row);
    }
    if (!page.more) break;
    const cursor = JSON.stringify(page.more);
    if (cursors.has(cursor)) throw new Error("VK повторил страницу «Моих треков». Попробуйте снова.");
    cursors.add(cursor);
    page = await api.audio.get({ owner_id: ownerId, raw: true, more: page.more });
  }
  return [...tracks.values()];
}

module.exports = { identity, normalizeAudios, ownTracks };
