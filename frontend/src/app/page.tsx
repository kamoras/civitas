import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import HeroSection from "@/components/home/HeroSection";
import WakeUpSection from "@/components/home/WakeUpSection";
import ManifestoSection from "@/components/home/ManifestoSection";

export default function Home() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main>
        <HeroSection />
        <WakeUpSection />
        <ManifestoSection />
      </main>
      <Footer />
    </>
  );
}
