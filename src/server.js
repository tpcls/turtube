import http from 'node:http';
import { URL } from 'node:url';

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || '127.0.0.1';
const USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

function sendJson(res, statusCode, body) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
  });
  res.end(statusCode === 204 ? undefined : JSON.stringify(body, null, 2));
}

function parseVideoId(input) {
  if (!input) return null;

  if (/^[a-zA-Z0-9_-]{11}$/.test(input)) {
    return input;
  }

  try {
    const url = new URL(input);

    if (url.hostname === 'youtu.be') {
      return url.pathname.split('/').filter(Boolean)[0] || null;
    }

    if (url.hostname.endsWith('youtube.com')) {
      if (url.pathname === '/watch') return url.searchParams.get('v');
      if (url.pathname.startsWith('/shorts/')) return url.pathname.split('/')[2] || null;
      if (url.pathname.startsWith('/embed/')) return url.pathname.split('/')[2] || null;
    }
  } catch {
    return null;
  }

  return null;
}

function getText(value) {
  if (!value) return undefined;
  if (typeof value.simpleText === 'string') return value.simpleText;
  if (Array.isArray(value.runs)) return value.runs.map((run) => run.text || '').join('');
  return undefined;
}

function parseJsonAfter(html, marker) {
  const markerIndex = html.indexOf(marker);
  if (markerIndex === -1) return null;

  const jsonStart = html.indexOf('{', markerIndex + marker.length);
  if (jsonStart === -1) return null;

  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let i = jsonStart; i < html.length; i += 1) {
    const char = html[i];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
    } else if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return JSON.parse(html.slice(jsonStart, i + 1));
      }
    }
  }

  return null;
}

function compactVideoDetails(videoDetails, microformat) {
  const renderer = microformat?.playerMicroformatRenderer || {};
  const viewCount = Number(videoDetails?.viewCount || renderer.viewCount || 0);
  const lengthSeconds = Number(videoDetails?.lengthSeconds || 0);

  return {
    id: videoDetails?.videoId,
    title: videoDetails?.title || renderer.title?.simpleText,
    description: videoDetails?.shortDescription || renderer.description?.simpleText,
    channelId: videoDetails?.channelId || renderer.externalChannelId,
    channelTitle: videoDetails?.author || renderer.ownerChannelName,
    publishedAt: renderer.publishDate || renderer.uploadDate,
    category: renderer.category,
    isLive: Boolean(videoDetails?.isLiveContent),
    durationSeconds: Number.isFinite(lengthSeconds) ? lengthSeconds : undefined,
    viewCount: Number.isFinite(viewCount) ? viewCount : undefined,
    thumbnails: videoDetails?.thumbnail?.thumbnails || renderer.thumbnail?.thumbnails || [],
    keywords: videoDetails?.keywords || [],
    links: {
      watch: `https://www.youtube.com/watch?v=${videoDetails?.videoId}`,
      embed: `https://www.youtube.com/embed/${videoDetails?.videoId}`
    }
  };
}

async function fetchText(url) {
  const response = await fetch(url, {
    headers: {
      'User-Agent': USER_AGENT,
      'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    }
  });

  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`);
  }

  return response.text();
}

async function getOembed(id) {
  const url = new URL('https://www.youtube.com/oembed');
  url.searchParams.set('url', `https://www.youtube.com/watch?v=${id}`);
  url.searchParams.set('format', 'json');

  const response = await fetch(url, {
    headers: {
      'User-Agent': USER_AGENT,
      'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
    }
  });

  if (!response.ok) return null;
  return response.json();
}

async function getVideo(reqUrl, res) {
  const rawInput = reqUrl.searchParams.get('id') || reqUrl.searchParams.get('url');
  const id = parseVideoId(rawInput);

  if (!id) {
    sendJson(res, 400, {
      error: 'Provide a valid YouTube video id or url query parameter.'
    });
    return;
  }

  const watchUrl = `https://www.youtube.com/watch?v=${id}&hl=ko&gl=KR`;
  const [html, oembed] = await Promise.all([fetchText(watchUrl), getOembed(id)]);
  const playerResponse = parseJsonAfter(html, 'ytInitialPlayerResponse');
  const initialData = parseJsonAfter(html, 'ytInitialData');

  if (!playerResponse?.videoDetails) {
    sendJson(res, 404, {
      error: 'Video details not found. The video may be private, blocked, or YouTube markup changed.',
      id
    });
    return;
  }

  const suggestions = [];
  if (initialData) {
    // Try specifically for watch page secondary results first
    const secondary = initialData.contents?.twoColumnWatchNextResults?.secondaryResults?.secondaryResults?.results;
    if (Array.isArray(secondary)) {
      for (const item of secondary) {
        collectSearchVideos(item, suggestions, new Set([id]));
      }
    }
    // Fallback to recursive search if empty
    if (suggestions.length === 0) {
      collectSearchVideos(initialData, suggestions, new Set([id]));
    }
  }

  sendJson(res, 200, {
    video: {
      ...compactVideoDetails(playerResponse.videoDetails, playerResponse.microformat),
      suggestions: suggestions.slice(0, 8),
      oembed: oembed
        ? {
            title: oembed.title,
            authorName: oembed.author_name,
            authorUrl: oembed.author_url,
            providerName: oembed.provider_name,
            thumbnailUrl: oembed.thumbnail_url,
            html: oembed.html
          }
        : null,
      playabilityStatus: playerResponse.playabilityStatus
        ? {
            status: playerResponse.playabilityStatus.status,
            reason: playerResponse.playabilityStatus.reason
          }
        : undefined
    }
  });
}

function collectSearchVideos(node, videos, seen) {
  if (!node || typeof node !== 'object') return;

  // Search for any known video renderer types
  const item = node.videoRenderer || node.compactVideoRenderer || node.gridVideoRenderer || node.playlistVideoRenderer;
  
  if (item && item.videoId && !seen.has(item.videoId)) {
    seen.add(item.videoId);
    videos.push({
      id: item.videoId,
      title: getText(item.title) || getText(item.headline) || 'Untitled',
      channelTitle: getText(item.ownerText) || 
                    getText(item.shortBylineText) || 
                    getText(item.longBylineText) || 
                    item.shortBylineText?.runs?.[0]?.text || 
                    item.longBylineText?.runs?.[0]?.text || 
                    'Unknown',
      publishedText: getText(item.publishedTimeText) || '',
      duration: getText(item.lengthText) || 
                item.lengthText?.simpleText || 
                item.thumbnailOverlays?.[0]?.thumbnailOverlayTimeStatusRenderer?.text?.simpleText || 
                '',
      viewCountText: getText(item.viewCountText || item.shortViewCountText) || '',
      thumbnails: item.thumbnail?.thumbnails || [],
      links: {
        watch: `https://www.youtube.com/watch?v=${item.videoId}`,
        embed: `https://www.youtube.com/embed/${item.videoId}`
      }
    });
    // We found a video, but we should still look into its children for more?
    // Usually not needed for these renderers.
  }

  // Recursive search
  if (Array.isArray(node)) {
    for (const child of node) collectSearchVideos(child, videos, seen);
  } else {
    for (const key in node) {
      if (key === 'videoDetails' || key === 'playerConfig') continue; // Skip large unrelated blocks
      collectSearchVideos(node[key], videos, seen);
    }
  }
}

async function searchVideos(reqUrl, res) {
  const q = reqUrl.searchParams.get('q');
  const maxResults = Math.min(Number(reqUrl.searchParams.get('maxResults') || 10), 100);

  if (!q) {
    sendJson(res, 400, { error: 'Provide a q query parameter.' });
    return;
  }

  const url = new URL('https://www.youtube.com/results');
  url.searchParams.set('search_query', q);
  url.searchParams.set('hl', 'ko');
  url.searchParams.set('gl', 'KR');

  const html = await fetchText(url);
  const initialData = parseJsonAfter(html, 'ytInitialData');

  if (!initialData) {
    sendJson(res, 502, { error: 'Could not parse YouTube search response.' });
    return;
  }

  const videos = [];
  collectSearchVideos(initialData, videos, new Set());

  sendJson(res, 200, {
    query: q,
    videos: videos.slice(0, maxResults)
  });
}

async function handleRequest(req, res) {
  if (req.method === 'OPTIONS') {
    sendJson(res, 204, {});
    return;
  }

  const reqUrl = new URL(req.url, `http://${req.headers.host}`);

  try {
    if (req.method === 'GET' && reqUrl.pathname === '/health') {
      sendJson(res, 200, { ok: true });
      return;
    }

    if (req.method === 'GET' && reqUrl.pathname === '/api/youtube/video') {
      await getVideo(reqUrl, res);
      return;
    }

    if (req.method === 'GET' && reqUrl.pathname === '/api/youtube/search') {
      await searchVideos(reqUrl, res);
      return;
    }

    sendJson(res, 404, {
      error: 'Not found.',
      endpoints: [
        'GET /health',
        'GET /api/youtube/video?id={videoId}',
        'GET /api/youtube/video?url={youtubeUrl}',
        'GET /api/youtube/search?q={keyword}'
      ]
    });
  } catch (error) {
    sendJson(res, error.statusCode || 500, {
      error: error.message
    });
  }
}

http.createServer(handleRequest).listen(PORT, HOST, () => {
  console.log(`Server listening on http://${HOST}:${PORT}`);
});
