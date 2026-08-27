/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 900: '#0a0c10', 800: '#0f1218', 700: '#151922', 600: '#1c2230', 500: '#252c3d' },
        line: '#2a3243',
        accent: { DEFAULT: '#38bdf8', soft: '#0ea5e9' },
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
