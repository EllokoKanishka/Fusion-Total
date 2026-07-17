export class ApiError extends Error {
  constructor(message, { data = null, status = 0, cause = null } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'ApiError';
    this.data = data;
    this.status = status;
  }
}

export async function api(path, body, requestOptions = {}) {
  const options = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  };
  Object.assign(options, requestOptions || {});
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw error;
    }
    throw new ApiError('network_error', { cause: error });
  }
  let data;
  try {
    data = await response.json();
  } catch (error) {
    throw new ApiError('invalid_json_response', { status: response.status, cause: error });
  }
  if (!response.ok || data.ok === false) {
    throw new ApiError(data.error || data.detail || data.message || 'request_failed', {
      data,
      status: response.status
    });
  }
  return data;
}
