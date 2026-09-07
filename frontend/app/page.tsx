import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { LiveStrip } from "@/components/landing/LiveStrip";
import { Problem } from "@/components/landing/Problem";
import { LivePreview } from "@/components/landing/LivePreview";
import { Capabilities } from "@/components/landing/Capabilities";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Measured } from "@/components/landing/Measured";
import { Scale } from "@/components/landing/Scale";
import { Security } from "@/components/landing/Security";
import { Cta } from "@/components/landing/Cta";
import { Footer } from "@/components/landing/Footer";

export default function Home() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <LiveStrip />
        <Problem />
        <LivePreview />
        <Capabilities />
        <HowItWorks />
        <Measured />
        <Scale />
        <Security />
        <Cta />
      </main>
      <Footer />
    </>
  );
}
