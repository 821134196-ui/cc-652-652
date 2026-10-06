<template>
  <div class="container">
    <div class="toolbar">
      <h2 style="margin:0">通知中心</h2>
      <button @click="load">刷新</button>
    </div>
    <div class="alert info" style="margin-top:0">
      签署完成、方案变更需重签、同意撤回三类事件都会推送给手术团队（本地模拟推送，见
      <span class="mono">backend/data/notify_gateway.log</span>）。
    </div>
    <div class="card">
      <table v-if="notes.length">
        <thead>
          <tr>
            <th style="width:160px">类型</th>
            <th style="width:70px">手术</th>
            <th>内容</th>
            <th style="width:150px">时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in notes" :key="n.id">
            <td><span class="badge" :class="badgeClass(n.type)">{{ label(n.type) }}</span></td>
            <td><RouterLink :to="`/surgeries/${n.surgery_id}`">#{{ n.surgery_id }}</RouterLink></td>
            <td>{{ n.message }}</td>
            <td class="muted">{{ fmt(n.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="muted" style="text-align:center;padding:24px">暂无通知</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const notes = ref([])

function label(t) {
  return { SIGNED: '签署完成', RESIGN_REQUIRED: '须重新签署', WITHDRAWN: '同意撤回' }[t] || t
}
function badgeClass(t) {
  return { SIGNED: 'READY', RESIGN_REQUIRED: 'RESIGN_REQUIRED', WITHDRAWN: 'WITHDRAWN' }[t] || 'UNSIGNED'
}
function fmt(iso) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  notes.value = await api.listNotifications()
}
onMounted(load)
</script>
