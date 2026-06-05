/**
 * Thin fetch wrapper that dispatches 'auth:unauthorized' on HTTP 401
 * so AuthContext can switch to the login page automatically.
 */
export async function apiFetch(url, options = {}) {
  const res = await fetch(url, options)
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth:unauthorized'))
    const err = new Error('Unauthorized')
    err.status = 401
    throw err
  }
  return res
}
