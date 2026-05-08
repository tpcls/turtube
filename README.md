# Tuebe YouTube Server

API 키 없이 서버에서 YouTube 공개 페이지와 oEmbed 엔드포인트를 요청해 영상 정보를 JSON으로 반환하는 Node.js 서버입니다.

## 실행

```bash
npm start
```

필요하면 포트를 바꿀 수 있습니다.

```bash
PORT=4000 HOST=127.0.0.1 npm start
```

## API

### 상태 확인

```bash
curl http://127.0.0.1:3000/health
```

### 영상 정보 가져오기

```bash
curl "http://127.0.0.1:3000/api/youtube/video?id=dQw4w9WgXcQ"
```

또는 YouTube URL을 넘길 수 있습니다.

```bash
curl "http://127.0.0.1:3000/api/youtube/video?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

반환 정보에는 제목, 설명, 채널, 썸네일, 업로드일, 길이, 조회수, 키워드, watch/embed 링크, oEmbed 정보가 포함됩니다.

### 영상 검색

```bash
curl "http://127.0.0.1:3000/api/youtube/search?q=nodejs&maxResults=5"
```

## 주의

이 방식은 YouTube 공식 Data API 키가 필요 없지만, YouTube 웹 페이지의 내장 JSON 구조를 파싱합니다. YouTube 페이지 구조가 바뀌면 검색이나 일부 메타데이터 파싱이 깨질 수 있습니다.

이 코드는 영상 파일을 다운로드하지 않습니다. 공개 메타데이터와 YouTube 재생 링크만 반환합니다.
