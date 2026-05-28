// Mirror of the backend ADVISORS list — used for loading skeletons.
// The backend now reports the actual model used (whichever provider answered),
// so the model field here is only shown during the loading state.
const ADVISORS_META = [
  {
    id:    'scout',
    name:  'The Scout',
    role:  'Signal validation & match context',
    model: 'ai-scout',
    emoji: '🔭',
  },
  {
    id:    'strategist',
    name:  'The Strategist',
    role:  'Portfolio construction & value ranking',
    model: 'ai-strategist',
    emoji: '♟️',
  },
  {
    id:    'skeptic',
    name:  'The Skeptic',
    role:  'Contrarian risk & red-flag analysis',
    model: 'ai-skeptic',
    emoji: '🧐',
  },
]

export default ADVISORS_META
