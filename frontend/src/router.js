import { createRouter, createWebHashHistory } from 'vue-router'
import ScheduleBoard from './views/ScheduleBoard.vue'
import SurgeryDetail from './views/SurgeryDetail.vue'
import NotificationsView from './views/NotificationsView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: ScheduleBoard },
    { path: '/surgeries/:id', component: SurgeryDetail, props: true },
    { path: '/notifications', component: NotificationsView },
  ],
})
