import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';

/**
 * The URL of one of the extension's server endpoints.
 *
 * The server mounts the routes under its `base_url` (e.g. `/user/<name>/` on
 * JupyterHub), so every request must be built from it (gh #170).
 *
 * @param endPoint The endpoint, e.g. `chat`
 * @param baseUrl The server's base URL; defaults to this page's
 */
export function apiUrl(
  endPoint = '',
  baseUrl = ServerConnection.makeSettings().baseUrl
): string {
  return URLExt.join(baseUrl, 'langstage-jupyter', endPoint);
}

/**
 * Call the API extension
 *
 * @param endPoint API REST end point for the extension
 * @param init Initial values for the request
 * @returns The response body interpreted as JSON
 */
export async function requestAPI<T>(
  endPoint = '',
  init: RequestInit = {}
): Promise<T> {
  // Make request to Jupyter API
  const settings = ServerConnection.makeSettings();
  const requestUrl = apiUrl(endPoint, settings.baseUrl);

  let response: Response;
  try {
    response = await ServerConnection.makeRequest(requestUrl, init, settings);
  } catch (error) {
    throw new ServerConnection.NetworkError(error as TypeError);
  }

  let data: any = await response.text();

  if (data.length > 0) {
    try {
      data = JSON.parse(data);
    } catch (error) {
      console.log('Not a JSON response body.', response);
    }
  }

  if (!response.ok) {
    throw new ServerConnection.ResponseError(response, data.message || data);
  }

  return data;
}

/**
 * POST a JSON body to a streaming (SSE) endpoint and return the open response.
 *
 * Goes through `ServerConnection` like {@link requestAPI}, so the request carries
 * the server's base URL, the XSRF header and any token (gh #170). A non-2xx
 * response throws, with a message for the sidebar to show.
 */
export async function postStream(endPoint: string, body: unknown): Promise<Response> {
  const settings = ServerConnection.makeSettings();
  const response = await ServerConnection.makeRequest(
    apiUrl(endPoint, settings.baseUrl),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    },
    settings
  );
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(
      requestFailedMessage(endPoint, response.status, response.statusText, text)
    );
  }
  return response;
}

/** The longest server message {@link requestFailedMessage} quotes. */
const MAX_DETAIL = 200;

/**
 * Describe a failed request: the status, plus the server's message when the body
 * is Jupyter's JSON error (`{"message": ...}`). An HTML error page adds nothing.
 */
export function requestFailedMessage(
  endPoint: string,
  status: number,
  statusText: string,
  body: string
): string {
  let detail = '';
  try {
    const data = JSON.parse(body);
    if (data && typeof data.message === 'string') {
      detail = data.message.trim();
    }
  } catch {
    // Not JSON, e.g. an HTML error page.
  }
  if (detail.length > MAX_DETAIL) {
    detail = `${detail.slice(0, MAX_DETAIL - 1)}…`;
  }
  const what = endPoint.charAt(0).toUpperCase() + endPoint.slice(1);
  const code = statusText ? `${status} ${statusText}` : `${status}`;
  return `${what} request failed: ${code}${detail ? ` (${detail})` : ''}`;
}
