import { useServer } from '../lib/server.jsx';

// Shown instead of a page that needs the API when the API is not reachable. There is deliberately no offline
// fallback: a second scoring engine in the browser would show numbers that differ from the server's.
export default function ServerNotice() {
  const { status, error, retry } = useServer();
  if (status === 'ready') return null;
  return (
    <div className="notice">
      {status === 'loading' ? (
        <p role="status">Connecting to the PathLens server… A free-tier host can take up to a minute to wake up.</p>
      ) : (
        <>
          <p className="error" role="alert">{error}</p>
          <p className="muted">
            Start the API with <code>uvicorn main:app --reload</code> in the backend folder, or set VITE_API_URL to where it is hosted.
          </p>
          <button className="ghost" onClick={retry}>Try again</button>
        </>
      )}
    </div>
  );
}
