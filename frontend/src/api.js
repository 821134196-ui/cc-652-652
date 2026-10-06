// Thin fetch wrapper for the local FastAPI backend.
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const message = data?.detail?.message || `请求失败（${res.status}）`
    const code = data?.detail?.code || 'ERROR'
    const err = new Error(message)
    err.code = code
    throw err
  }
  return data
}

export const api = {
  listPatients: () => request('/api/patients'),
  createPatient: (body) => request('/api/patients', { method: 'POST', body }),

  addFamily: (patientId, body) =>
    request(`/api/patients/${patientId}/family`, { method: 'POST', body }),
  setFamilyAuth: (familyId, body) =>
    request(`/api/family/${familyId}/authorization`, { method: 'PATCH', body }),

  createSurgery: (body) => request('/api/surgeries', { method: 'POST', body }),
  listSurgeries: () => request('/api/surgeries'),
  getSurgery: (id) => request(`/api/surgeries/${id}`),

  createVersion: (surgeryId, body) =>
    request(`/api/surgeries/${surgeryId}/versions`, { method: 'POST', body }),
  sign: (surgeryId, body) =>
    request(`/api/surgeries/${surgeryId}/sign`, { method: 'POST', body }),
  withdraw: (surgeryId, body) =>
    request(`/api/surgeries/${surgeryId}/withdraw`, { method: 'POST', body }),

  listNotifications: (surgeryId) =>
    request(`/api/notifications${surgeryId ? `?surgery_id=${surgeryId}` : ''}`),
  markRead: (id) => request(`/api/notifications/${id}/read`, { method: 'POST' }),
}
