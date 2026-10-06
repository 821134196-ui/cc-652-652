/* global Vue */
const { createApp } = Vue;

createApp({
  data() {
    return {
      view: 'schedule',
      busy: false,
      toast: null,
      surgeries: [],
      patients: [],
      notifications: [],
      detail: null,
      showNewSurgery: false,
      showNewVersion: false,
      newSurgery: { patient_id: '', title: '', surgeon: '', scheduled_at: '' },
      newVersion: { procedure: '', anesthesia: '', risks: '', created_by: '' },
      signForm: { signer_role: 'PATIENT', signer_name: '', relationship: '' },
      lastSign: null, // { version_id, payload }
      withdrawForm: { withdrawn_by: '', reason: '' },
      familyForm: { name: '', relationship: '' },
      stateMeta: {
        READY: { label: '已签署·有效', cls: 'ok-bg' },
        PENDING_SIGNATURE: { label: '待签署', cls: 'warn-bg' },
        WITHDRAWN: { label: '已撤回·需重新出具并签署', cls: 'bad-bg' },
        NO_VERSION: { label: '未出具同意书', cls: 'muted-bg' },
      },
      versionStatusMeta: {
        ACTIVE: { label: '当前有效', cls: 'ok-bg' },
        SUPERSEDED: { label: '已被取代', cls: 'muted-bg' },
        WITHDRAWN: { label: '已撤回', cls: 'bad-bg' },
      },
      notifyMeta: {
        VERSION_CREATED: { label: '新版本', cls: 'info-bg' },
        VERSION_SUPERSEDED: { label: '方案变更', cls: 'warn-bg' },
        CONSENT_SIGNED: { label: '已签署', cls: 'ok-bg' },
        CONSENT_WITHDRAWN: { label: '已撤回', cls: 'bad-bg' },
        SIGN_REJECTED: { label: '拒签告警', cls: 'bad-bg' },
      },
    };
  },
  computed: {
    versionsDesc() {
      return this.detail ? [...this.detail.versions].reverse() : [];
    },
    activeVersion() {
      return this.detail ? this.detail.versions.find(v => v.status === 'ACTIVE') : null;
    },
    activeValidSig() {
      return this.activeVersion
        ? this.activeVersion.signatures.find(s => s.status === 'VALID') || null
        : null;
    },
    surgeryNotifications() {
      return this.detail
        ? this.notifications.filter(n => n.surgery_id === this.detail.id)
        : [];
    },
  },
  methods: {
    async api(path, opts = {}) {
      const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
      if (!res.ok) {
        let msg = `${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
      }
      return res.json();
    },
    showToast(text, type = 'ok') {
      this.toast = { text, type };
      setTimeout(() => { this.toast = null; }, 4200);
    },
    async run(fn, okMsg) {
      this.busy = true;
      try {
        await fn();
        if (okMsg) this.showToast(okMsg, 'ok');
      } catch (e) {
        this.showToast(e.message, 'err');
      } finally {
        this.busy = false;
      }
    },
    async loadSurgeries() { this.surgeries = await this.api('/api/surgeries'); },
    async loadPatients() { this.patients = await this.api('/api/patients'); },
    async loadNotifications() { this.notifications = await this.api('/api/notifications'); },
    async refreshAll() {
      await Promise.all([this.loadSurgeries(), this.loadPatients(), this.loadNotifications()]);
      if (this.detail) await this.refreshDetail(this.detail.id);
    },
    async refreshDetail(id) {
      this.detail = await this.api(`/api/surgeries/${id}`);
    },
    async openDetail(id) {
      await this.run(async () => {
        await this.refreshDetail(id);
        this.view = 'detail';
        this.showNewVersion = false;
        this.lastSign = null;
        this.signForm = { signer_role: 'PATIENT', signer_name: '', relationship: '' };
        this.withdrawForm = { withdrawn_by: this.detail.patient.name, reason: '' };
        this.newVersion.created_by = this.detail.surgeon;
      });
    },
    switchView(v) { this.view = v; },

    async createSurgery() {
      await this.run(async () => {
        const s = await this.api('/api/surgeries', { method: 'POST', body: {
          patient_id: Number(this.newSurgery.patient_id),
          title: this.newSurgery.title,
          surgeon: this.newSurgery.surgeon,
          scheduled_at: this.newSurgery.scheduled_at,
        }});
        this.newSurgery = { patient_id: '', title: '', surgeon: '', scheduled_at: '' };
        this.showNewSurgery = false;
        await this.loadSurgeries();
        await this.openDetail(s.id);
      }, '手术已创建');
    },

    async createVersion() {
      await this.run(async () => {
        await this.api(`/api/surgeries/${this.detail.id}/consent-versions`, {
          method: 'POST', body: this.newVersion,
        });
        this.newVersion = { procedure: '', anesthesia: '', risks: '', created_by: this.detail.surgeon };
        this.showNewVersion = false;
        await Promise.all([this.refreshDetail(this.detail.id), this.loadSurgeries(), this.loadNotifications()]);
      }, '新版本已出具，旧版本及原签署已失效');
    },

    async signActive() {
      const v = this.activeVersion;
      if (!v) return;
      await this.run(async () => {
        // ① 本地模拟签名服务：签发绑定 签署人+文档哈希 的回执
        const receipt = await this.api('/api/esign/receipts', { method: 'POST', body: {
          signer_name: this.signForm.signer_name,
          document_hash: v.content_hash,
        }});
        // ② 携带回执提交签署
        const payload = {
          signer_name: this.signForm.signer_name,
          signer_role: this.signForm.signer_role,
          relationship: this.signForm.signer_role === 'FAMILY' ? (this.signForm.relationship || null) : null,
          signature_text: receipt.signature_text,
          receipt_id: receipt.receipt_id,
        };
        await this.api(`/api/consent-versions/${v.id}/sign`, { method: 'POST', body: payload });
        this.lastSign = { version_id: v.id, payload };
        await Promise.all([this.refreshDetail(this.detail.id), this.loadSurgeries(), this.loadNotifications()]);
      }, '签署成功，当前版本已满足手术准备条件');
    },

    async replayReceipt() {
      if (!this.lastSign) return;
      await this.run(async () => {
        const r = await this.api(`/api/consent-versions/${this.lastSign.version_id}/sign`, {
          method: 'POST', body: this.lastSign.payload,
        });
        this.showToast(r.idempotent_replay
          ? `重复回执已幂等处理：仍为签名 #${r.id}，未产生第二份有效文件`
          : '已受理', 'ok');
        await Promise.all([this.refreshDetail(this.detail.id), this.loadSurgeries()]);
      });
    },

    async withdraw() {
      const v = this.activeVersion;
      if (!v) return;
      await this.run(async () => {
        await this.api(`/api/consent-versions/${v.id}/withdraw`, {
          method: 'POST', body: this.withdrawForm,
        });
        await Promise.all([this.refreshDetail(this.detail.id), this.loadSurgeries(), this.loadNotifications()]);
      }, '已撤回：文件失效并已通知手术团队');
    },

    async addFamily() {
      await this.run(async () => {
        await this.api(`/api/patients/${this.detail.patient.id}/authorized-family`, {
          method: 'POST', body: this.familyForm,
        });
        this.familyForm = { name: '', relationship: '' };
        await Promise.all([this.refreshDetail(this.detail.id), this.loadPatients()]);
      }, '已添加授权家属');
    },

    toggleNewVersion() {
      this.showNewVersion = !this.showNewVersion;
      if (this.showNewVersion && this.activeVersion) {
        // 预填当前内容，模拟"方案变更"修改场景
        this.newVersion = {
          procedure: this.activeVersion.procedure,
          anesthesia: this.activeVersion.anesthesia,
          risks: this.activeVersion.risks,
          created_by: this.detail.surgeon,
        };
      }
    },

    pad(n) { return String(n).padStart(2, '0'); },
    fmt(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      const p = x => String(x).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    },
  },
  async mounted() {
    await this.refreshAll();
    setInterval(() => { if (!this.busy) this.refreshAll(); }, 8000);
  },
}).mount('#app');
