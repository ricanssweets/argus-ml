import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Charcoal — brand dark neutrals
        ink: {
          950: "#0d0d0f",
          900: "#141416",
          850: "#1a1a1e",
          800: "#232328",
          700: "#313138",
        },
        // Bronze — brand primary accent
        bronze: {
          200: "#e8c9a0",
          300: "#d9ae7e",
          400: "#c99763",
          500: "#bd8250",
          600: "#a56e40",
          700: "#855834",
        },
        // Deep Teal — brand secondary
        teal: {
          700: "#175049",
          800: "#123e39",
          900: "#0d2f2c",
        },
        // Stone — brand light neutral
        stone: {
          100: "#f2ede1",
          200: "#e9e2d5",
          300: "#d8cfbc",
        },
      },
    },
  },
  plugins: [],
};

export default config;
