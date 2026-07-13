import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import HeroSection from "@/components/home/HeroSection";
import ManifestoSection from "@/components/home/ManifestoSection";
import ActionPreview from "@/components/home/ActionPreview";
import WakeUpSection from "@/components/home/WakeUpSection";

export default function Home() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1}>
        <HeroSection />
        <ManifestoSection />
        <ActionPreview />
        <WakeUpSection />
      </main>
      <Footer />
    </>
  );
}
