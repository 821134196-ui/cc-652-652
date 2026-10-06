<template>
  <div class="container" v-if="data">
    <RouterLink to="/" class="muted" style="display:inline-block;margin-bottom:10px">← 返回排期看板</RouterLink>

    <div class="card">
      <div class="toolbar" style="margin-bottom:6px">
        <div>
          <h2 style="margin:0 0 2px">{{ data.surgery.name }}</h2>
          <div class="muted">患者：{{ data.patient.name }}（{{ data.patient.mrn }}） · 手术 #{{ data.surgery.id }}</div>
        </div>
        <ReadinessBadge :code="data.readiness.readiness" />
      </div>
      <div class="alert" :class="alertClass">
        <strong>排期判定：</strong>{{ data.readiness.message }}
      </div>
    </div>

    <div class="row" style="align-items:flex-start">
      <!-- 左：同意书版本时间线 -->
      <div class="card" style="flex:2;min-width:340px">
        <div class="toolbar">
          <h2 style="margin:0">同意书版本</h2>
          <button class="primary small" @click="openVersionModal">＋ 生成新版同意书</button>
        </div>

        <div class="alert info" v-if="data.versions.length === 0">
          医生尚未生成同意书。请先由医生创建第 1 版（含术式、麻醉方式与风险告知）。
        </div>

        <div class="timeline">
          <div v-for="v in [...data.versions].reverse()" :key="v.id" class="version-item" :class="v.status">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <strong>第 {{ v.version_no }} 版</strong>
              <span class="badge neutral" v-if="v.status==='ACTIVE'">当前版本</span>
              <span class="badge" style="background:var(--warn-bg);color:var(--warn)" v-else-if="v.status==='SUPERSEDED'">已被新版替代</span>
              <span class="badge no" v-else>已撤回（文件留存）</span>
              <span class="muted">{{ fmt(v.created_at) }} · {{ v.created_by }}</span>
            </div>
            <div v-if="v.change_summary" class="alert warn" style="margin:6px 0;padding:7px 11px">
              变更说明：{{ v.change_summary }}
            </div>
            <dl class="doc-box">
              <dt>术式</dt><dd>{{ v.procedure }}</dd>
              <dt>麻醉方式</dt><dd>{{ v.anesthesia }}</dd>
              <dt>风险告知</dt><dd>{{ v.risks }}</dd>
            </dl>

            <!-- 签署状态 -->
            <div v-if="v.signature" style="margin-top:8px">
              <div class="alert" :class="v.signature.invalidated_at ? 'danger' : 'ok'">
                <template v-if="v.signature.invalidated_at">
                  该签名已失效（{{ fmt(v.signature.invalidated_at) }}）——
                  <template v-if="v.status==='SUPERSEDED'">方案变更，旧签名不满足当前手术准备条件；</template>
                  <template v-else>同意已撤回；</template>
                  历史文件保留，不得复制到新版。
                </template>
                <template v-else>
                  有效签署：{{ v.signature.signer_name }}
                  （{{ v.signature.signer_type === 'PATIENT' ? '患者本人' : '授权家属' }}）
                  · {{ fmt(v.signature.signed_at) }}
                  · 回执 <span class="mono">{{ v.signature.gateway_receipt_id }}</span>
                </template>
                <div class="muted mono" style="margin-top:4px">文件：{{ v.signature.document_path }}</div>
              </div>
            </div>
            <div v-else-if="v.status === 'ACTIVE'" style="margin-top:8px">
              <div class="alert warn" style="margin-bottom:8px">
                当前版本尚未签署，不能排期。请患者本人或<strong>已获授权</strong>家属完成签署。
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <button class="primary small" @click="openSign('PATIENT')">患者本人签署</button>
                <select v-model="signFamilyId" style="width:auto;min-width:170px">
                  <option :value="null" disabled>选择家属…</option>
                  <option v-for="f in data.family_members" :key="f.id" :value="f.id">
                    {{ f.name }}（{{ f.relationship }}）{{ f.authorized ? '· 已授权' : '· 未授权' }}
                  </option>
                </select>
                <button class="small" :disabled="signFamilyId === null" @click="openSign('FAMILY')">家属签署</button>
              </div>
            </div>
          </div>
        </div>

        <!-- 撤回区 -->
        <div v-if="activeSigned" style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
          <h3 style="color:var(--danger)">撤回同意</h3>
          <div class="row" style="align-items:flex-end">
            <label class="field" style="flex:2">
              <span>撤回人</span>
              <select v-model="withdrawForm.signerType" style="width:100%">
                <option value="PATIENT">患者本人：{{ data.patient.name }}</option>
                <option v-for="f in authorizedFamily" :key="f.id" :value="'FAMILY:'+f.id">
                  授权家属：{{ f.name }}（{{ f.relationship }}）
                </option>
              </select>
            </label>
            <label class="field" style="flex:3">
              <span>撤回原因（可选）</span>
              <input v-model="withdrawForm.reason" placeholder="如：患者决定暂缓手术" />
            </label>
            <button class="danger" @click="doWithdraw">撤回同意并失效文件</button>
          </div>
        </div>
      </div>

      <!-- 右：家属授权管理 + 通知 -->
      <div style="flex:1;min-width:260px">
        <div class="card">
          <h2>家属与授权</h2>
          <div class="alert info" style="margin-top:0">
            只有<strong>已授权</strong>家属可完成有效签署；未授权家属的签署请求会被系统拒绝。
          </div>
          <table v-if="data.family_members.length">
            <thead><tr><th>姓名/关系</th><th style="width:80px">授权</th><th style="width:70px"></th></tr></thead>
            <tbody>
              <tr v-for="f in data.family_members" :key="f.id">
                <td>
                  {{ f.name }}
                  <div class="muted">{{ f.relationship }}<template v-if="f.authorization_note"> · {{ f.authorization_note }}</template></div>
                </td>
                <td><span class="badge" :class="f.authorized ? 'ok' : 'no'">{{ f.authorized ? '已授权' : '未授权' }}</span></td>
                <td>
                  <button class="small" @click="toggleAuth(f)">{{ f.authorized ? '撤销' : '授权' }}</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="muted" v-else>暂未登记家属</div>

          <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">
            <label class="field"><span>家属姓名</span><input v-model="familyForm.name" /></label>
            <label class="field"><span>与患者关系</span><input v-model="familyForm.relationship" placeholder="如：配偶 / 子女" /></label>
            <label class="field" style="display:flex;align-items:center;gap:8px;flex-direction:row">
              <input type="checkbox" v-model="familyForm.authorized" style="width:auto" />
              <span style="margin:0">已核验授权委托书，允许有效签署</span>
            </label>
            <button @click="addFamily">登记家属</button>
          </div>
        </div>

        <div class="card">
          <h2>本手术通知</h2>
          <div v-if="notifications.length === 0" class="muted">暂无通知</div>
          <div v-for="n in notifications" :key="n.id" class="alert" :class="noteClass(n.type)" style="margin:6px 0">
            <div class="muted" style="font-size:12px">{{ noteLabel(n.type) }} · {{ fmt(n.created_at) }}</div>
            {{ n.message }}
          </div>
        </div>
      </div>
    </div>

    <!-- 生成新版弹窗 -->
    <dialog id="version-modal" ref="versionModal">
      <div class="modal-body">
        <h3>生成第 {{ nextVersionNo }} 版同意书</h3>
        <div class="alert danger" v-if="data.versions.length">
          <strong>关键内容修改后：</strong>提交即把第 {{ data.versions.length }} 版置为「已替代」，
          其签名对当前方案失效，排期页将提示重新签字，旧签名不会被复制到新版。
        </div>
        <label class="field"><span>术式 *</span><textarea v-model="versionForm.procedure"></textarea></label>
        <label class="field"><span>麻醉方式 *</span><input v-model="versionForm.anesthesia" /></label>
        <label class="field"><span>风险告知 *</span><textarea v-model="versionForm.risks"></textarea></label>
        <label class="field">
          <span>本次修改说明（方案变更时建议填写）</span>
          <input v-model="versionForm.change_summary" placeholder="如：术中评估转为开腹，术式与麻醉变更" />
        </label>
        <div class="modal-actions">
          <button @click="versionModal.close()">取消</button>
          <button class="primary" :disabled="versionLoading" @click="createVersion">生成并通知团队</button>
        </div>
      </div>
    </dialog>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import ReadinessBadge from '../components/ReadinessBadge.vue'

const props = defineProps({ id: { type: [String, Number], required: true } })
const toast = inject('toast')

const data = ref(null)
const notifications = ref([])
const signFamilyId = ref(null)
const versionModal = ref(null)
const versionLoading = ref(false)

const versionForm = reactive({ procedure: '', anesthesia: '', risks: '', change_summary: '' })
const familyForm = reactive({ name: '', relationship: '', authorized: false })
const withdrawForm = reactive({ signerType: 'PATIENT', reason: '' })

const activeVersion = computed(() => data.value?.versions.find((v) => v.status === 'ACTIVE') || null)
const activeSigned = computed(() => !!(activeVersion.value?.signature && !activeVersion.value.signature.invalidated_at))
const authorizedFamily = computed(() => data.value?.family_members.filter((f) => f.authorized) || [])
const nextVersionNo = computed(() => (data.value ? data.value.versions.length + 1 : 1))

const alertClass = computed(() => ({
  READY: 'ok', RESIGN_REQUIRED: 'warn', UNSIGNED: 'info',
  WITHDRAWN: 'danger', NOT_CREATED: 'info',
}[data.value.readiness.readiness]))

function noteClass(type) {
  return { SIGNED: 'ok', RESIGN_REQUIRED: 'warn', WITHDRAWN: 'danger' }[type] || 'info'
}
function noteLabel(type) {
  return { SIGNED: '签署完成', RESIGN_REQUIRED: '须重新签署', WITHDRAWN: '同意撤回' }[type] || type
}
function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : ''
}

async function load() {
  data.value = await api.getSurgery(props.id)
  notifications.value = await api.listNotifications(props.id)
  // 预填新版表单为当前内容，方便医生在旧方案上修改
  const cur = activeVersion.value
  if (cur) {
    versionForm.procedure = cur.procedure
    versionForm.anesthesia = cur.anesthesia
    versionForm.risks = cur.risks
    versionForm.change_summary = ''
  }
}

function openVersionModal() {
  const cur = activeVersion.value
  if (cur) {
    versionForm.procedure = cur.procedure
    versionForm.anesthesia = cur.anesthesia
    versionForm.risks = cur.risks
    versionForm.change_summary = ''
  }
  versionModal.value.showModal()
}

async function createVersion() {
  if (!versionForm.procedure || !versionForm.anesthesia || !versionForm.risks) {
    toast.error('术式、麻醉方式、风险告知均为必填')
    return
  }
  versionLoading.value = true
  try {
    await api.createVersion(props.id, { ...versionForm })
    toast.ok(`第 ${nextVersionNo.value} 版同意书已生成，旧版签署已失效，已通知团队重新签字`)
    versionModal.value.close()
    await load()
  } catch (e) {
    toast.error(e.message)
  } finally {
    versionLoading.value = false
  }
}

async function openSign(signerType) {
  const body = { signer_type: signerType }
  if (signerType === 'FAMILY') {
    if (signFamilyId.value === null) {
      toast.error('请先选择家属')
      return
    }
    body.family_id = signFamilyId.value
  }
  try {
    const res = await api.sign(props.id, body)
    if (res.created) {
      toast.ok(`签署成功（回执 ${res.signature.gateway_receipt_id}），已生成有效同意书文件`)
    } else {
      toast.error('该版本已存在有效签署，本次请求按重复提交处理，未生成第二份文件')
    }
    await load()
  } catch (e) {
    if (e.code === 'UNAUTHORIZED_FAMILY') {
      toast.error('该家属未获授权，不能完成有效签署')
    } else {
      toast.error(e.message)
    }
    await load()
  }
}

async function doWithdraw() {
  const body = { reason: withdrawForm.reason || null }
  if (withdrawForm.signerType.startsWith('FAMILY:')) {
    body.signer_type = 'FAMILY'
    body.family_id = Number(withdrawForm.signerType.split(':')[1])
  } else {
    body.signer_type = 'PATIENT'
  }
  if (!confirm('确认撤回同意？撤回后原同意书立即失效并通知手术团队，历史文件仍会保留。')) return
  try {
    await api.withdraw(props.id, body)
    toast.ok('同意已撤回，手术团队已收到通知，原文件已失效（历史留存）')
    withdrawForm.reason = ''
    await load()
  } catch (e) {
    toast.error(e.code === 'UNAUTHORIZED_FAMILY' ? '该家属未获授权，不能代为撤回' : e.message)
  }
}

async function toggleAuth(f) {
  try {
    await api.setFamilyAuth(f.id, {
      authorized: !f.authorized,
      authorization_note: f.authorized ? '授权已撤销' : (f.authorization_note || '授权委托书已核验'),
    })
    toast.ok(f.authorized ? `已撤销「${f.name}」的签署授权` : `「${f.name}」已获授权`)
    await load()
  } catch (e) {
    toast.error(e.message)
  }
}

async function addFamily() {
  if (!familyForm.name || !familyForm.relationship) {
    toast.error('请填写家属姓名与关系')
    return
  }
  try {
    await api.addFamily(data.value.patient.id, { ...familyForm })
    toast.ok('家属已登记')
    familyForm.name = ''
    familyForm.relationship = ''
    familyForm.authorized = false
    await load()
  } catch (e) {
    toast.error(e.message)
  }
}

onMounted(load)
</script>
