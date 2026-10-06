<template>
  <header class="app-header">
    <div>
      <h1>手术知情同意版本与撤回系统</h1>
      <div class="subtitle">同一手术仅一份当前有效同意书 · 方案变更须重新签署</div>
    </div>
    <nav>
      <RouterLink to="/">手术排期</RouterLink>
      <RouterLink to="/notifications">通知中心</RouterLink>
    </nav>
  </header>

  <RouterView />

  <div class="toast-host">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{ t.text }}</div>
  </div>
</template>

<script setup>
import { provide, reactive } from 'vue'

let seq = 1
const toasts = reactive([])

function push(kind, text, ms = 3200) {
  const id = seq++
  toasts.push({ id, kind, text })
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id)
    if (i >= 0) toasts.splice(i, 1)
  }, ms)
}

provide('toast', {
  ok: (t) => push('ok', t),
  error: (t) => push('error', t),
})
</script>
