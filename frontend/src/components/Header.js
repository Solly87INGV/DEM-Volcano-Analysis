// Header.js
import React from 'react';
import './Header.css';

const Header = () => {
  const headerStyle = {
    backgroundImage: `url(${process.env.PUBLIC_URL + '/images/ingv_Etna.jpg'})`, // Usa process.env.PUBLIC_URL per referenziare la cartella public
    backgroundSize: 'cover',
    backgroundPosition: 'center',
  };

  return (
    <header className="app-header" style={headerStyle}>
      <img src={`${process.env.PUBLIC_URL}/images/logo-ingv.jpeg`} alt="Logo INGV" className="logo" />
      <h1>MorphoVolc DEM interface</h1>
    </header>
  );
};

export default Header;
