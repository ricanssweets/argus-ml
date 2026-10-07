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
        ink: {
          950: "#0a0e14",
          900: "#0f141d",
          850: "#141b26",
          800: "#1a2230",
          700: "#243041",
        },
      },
    },
  },
  plugins: [],
};

export default config;
