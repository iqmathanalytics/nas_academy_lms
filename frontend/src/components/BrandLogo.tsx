import React from "react";
import nasLogo from "../assets/nas-logo.jpeg";

interface BrandLogoProps {
    size?: "sm" | "md" | "lg" | "xl";
    showTagline?: boolean;
    className?: string;
}

const BrandLogo: React.FC<BrandLogoProps> = ({ size = "md", showTagline = false, className = "" }) => {
    const sizeClasses = {
        sm: "h-8",
        md: "h-10",
        lg: "h-14",
        xl: "h-20",
    };

    return (
        <div className={`flex flex-col items-start leading-none ${className}`}>
            <img
                src={nasLogo}
                alt="NAS Academy"
                className={`${sizeClasses[size]} w-auto object-contain`}
            />
            {showTagline && (
                <span className={`text-[#87C232] font-semibold tracking-wider ${size === 'xl' ? 'text-xl mt-1' : 'text-[0.6rem] mt-0.5'}`}>
                    NAS Academy
                </span>
            )}
        </div>
    );
};

export default BrandLogo;
