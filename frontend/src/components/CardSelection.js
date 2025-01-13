// CardSelection.js
import React from 'react';
import { Box, Typography } from '@mui/material';
import './CardSelection.css';

const CardSelection = ({ title, description, onClick, imageSrc, isSelected }) => {
  return (
    <Box 
      className={`card-selection ${isSelected ? 'selected' : ''}`} 
      onClick={onClick}
    >
      <img src={imageSrc} alt={title} className="card-image" />
      <Typography variant="h6" className="card-title">{title}</Typography>
      <Typography variant="body2" className="card-description">{description}</Typography>
    </Box>
  );
};

export default CardSelection;
