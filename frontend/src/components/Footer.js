// Footer.js
import React from 'react';
import './Footer.css';

const Footer = () => {
  return (
    <footer className="footer">
      <div className="footer-center">
        <p>&copy; 2023 MyDEMProject. All rights reserved.</p>
        <div className="footer-links">
          <a href="/privacy-policy">Privacy Policy</a>
          <a href="/terms-of-service">Terms of Service</a>
          <a href="/contact">Contact</a>
        </div>
      </div>

      <img
        src={`${process.env.PUBLIC_URL}/images/INGV_footer_white.png`}
        alt="Istituto Nazionale di Geofisica e Vulcanologia"
        className="footer-logo"
      />

      <div className="footer-info">
        <p>Site Manager: Marco Solinas</p>
      </div>
    </footer>
  );
};

export default Footer;