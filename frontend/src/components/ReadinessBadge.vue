<template>
  <span class="badge" :class="code">{{ label }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  code: { type: String, required: true },
})

const LABELS = {
  READY: '✓ 已签署 · 可排期',
  RESIGN_REQUIRED: '⚠ 方案变更 · 须重新签字',
  UNSIGNED: '◔ 待签署',
  WITHDRAWN: '✕ 已撤回 · 禁止排期',
  NOT_CREATED: '— 尚未生成同意书',
}

const label = computed(() => LABELS[props.code] || props.code)
</script>
