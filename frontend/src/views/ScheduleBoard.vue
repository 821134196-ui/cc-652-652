<template>
  <div class="container">
    <div class="toolbar">
      <h2 style="margin:0">手术排期看板</h2>
      <div class="filters">
        <button
          v-for="f in FILTERS" :key="f.value"
          :class="{ active: filter === f.value }"
          @click="filter = f.value"
        >{{ f.label }}</button>
      </div>
    </div>

    <div class="alert warn">
      <strong>排期规则：</strong>只有当前版本同意书完成有效签署（绿色）的手术可以安排。
      方案变更后旧签署自动失效，必须重新签字；<strong>禁止凭旧同意书排期，禁止把旧签名复制到新版</strong>。
    </div>

    <div class="card">
      <table>
        <thead>
          <tr>
            <th style="width:60px">#</th>
            <th>患者</th>
            <th>手术</th>
            <th style="width:190px">同意状态</th>
            <th>系统判定 / 提示</th>
            <th style="width:120px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in shown" :key="item.surgery.id" class="clickable" @click="open(item.surgery.id)">
            <td class="muted">{{ item.surgery.id }}</td>
            <td>{{ item.patient_name }}</td>
            <td>{{ item.surgery.name }}</td>
            <td><ReadinessBadge :code="item.readiness.readiness" /></td>
            <td :class="{ 'muted': item.readiness.readiness === 'READY' }">
              <span :style="item.readiness.readiness === 'READY' ? '' : 'font-weight:600'">
                {{ item.readiness.message }}
              </span>
            </td>
            <td><button class="small" @click.stop="open(item.surgery.id)">进入处理</button></td>
          </tr>
          <tr v-if="shown.length === 0">
            <td colspan="6" class="muted" style="text-align:center;padding:28px">暂无匹配的手术</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="row">
      <div class="card">
        <h2>新建患者</h2>
        <label class="field"><span>姓名</span><input v-model="patientForm.name" placeholder="如：张三" /></label>
        <label class="field"><span>病历号</span><input v-model="patientForm.mrn" placeholder="如：MRN-0001" /></label>
        <button class="primary" :disabled="patientLoading" @click="createPatient">创建患者</button>
      </div>

      <div class="card">
        <h2>登记手术</h2>
        <label class="field">
          <span>患者</span>
          <select v-model="surgeryForm.patientId">
            <option :value="null" disabled>请选择患者</option>
            <option v-for="p in patients" :key="p.id" :value="p.id">{{ p.name }}（{{ p.mrn }}）</option>
          </select>
        </label>
        <label class="field"><span>手术名称</span><input v-model="surgeryForm.name" placeholder="如：腹腔镜胆囊切除术" /></label>
        <button class="primary" :disabled="surgeryLoading" @click="createSurgery">登记并进入</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, inject } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import ReadinessBadge from '../components/ReadinessBadge.vue'

const toast = inject('toast')
const router = useRouter()

const items = ref([])
const patients = ref([])
const filter = ref('ALL')
const patientLoading = ref(false)
const surgeryLoading = ref(false)
const patientForm = reactive({ name: '', mrn: '' })
const surgeryForm = reactive({ patientId: null, name: '' })

const FILTERS = [
  { value: 'ALL', label: '全部' },
  { value: 'READY', label: '可排期' },
  { value: 'RESIGN_REQUIRED', label: '须重签' },
  { value: 'UNSIGNED', label: '待签署' },
  { value: 'WITHDRAWN', label: '已撤回' },
]

const shown = computed(() =>
  filter.value === 'ALL' ? items.value : items.value.filter((i) => i.readiness.readiness === filter.value)
)

async function load() {
  ;[items.value, patients.value] = await Promise.all([api.listSurgeries(), api.listPatients()])
}

function open(id) {
  router.push(`/surgeries/${id}`)
}

async function createPatient() {
  if (!patientForm.name || !patientForm.mrn) {
    toast.error('请填写姓名和病历号')
    return
  }
  patientLoading.value = true
  try {
    const p = await api.createPatient({ name: patientForm.name, mrn: patientForm.mrn })
    toast.ok(`患者「${p.name}」已创建`)
    patientForm.name = ''
    patientForm.mrn = ''
    surgeryForm.patientId = p.id
    await load()
  } catch (e) {
    toast.error(e.message)
  } finally {
    patientLoading.value = false
  }
}

async function createSurgery() {
  if (!surgeryForm.patientId || !surgeryForm.name) {
    toast.error('请选择患者并填写手术名称')
    return
  }
  surgeryLoading.value = true
  try {
    const s = await api.createSurgery({ patient_id: surgeryForm.patientId, name: surgeryForm.name })
    toast.ok('手术已登记')
    surgeryForm.name = ''
    await load()
    open(s.id)
  } catch (e) {
    toast.error(e.message)
  } finally {
    surgeryLoading.value = false
  }
}

onMounted(load)
</script>
