package practicas;

import java.util.Scanner;

public class funcionseguro {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        double preciobase = 100;

        System.out.println("Cual es tu edad");
        int edad = sc.nextInt();
        System.out.println("Cual es tu antiguedad con el seguro");
        int anyosSeguros = sc.nextInt();
        sc.nextLine();
        System.out.println("Tipo de seguro: Basico, Intermedio o Premium");
        String tipoSeguro = sc.nextLine();
        System.out.println("Cual es la edad del coche");
        int edadCoche = sc.nextInt();
        sc.nextLine();
        System.out.println("Cual es el color del coche");
        String colorCoche = sc.nextLine();
        System.out.println("Has tenido accidentes? TRUE/FALSE");
        boolean accidentes = sc.nextBoolean();

        double preciofinal = calcularSeguro(edad, accidentes, tipoSeguro, anyosSeguros, edadCoche, colorCoche, preciobase);

        System.out.println(preciofinal);
    }
    public static double calcularSeguro (int edad, boolean accidentes, String tipoSeguro, int anyosSeguros, int edadCoche, String colorCoche, double preciobase) {
        if (edad >= 19 && edad < 25 && tipoSeguro.equalsIgnoreCase("Basico")) {
            return (30 * preciobase) / 100 + preciobase;
        } else if (edad >= 25 && edad <= 40 && tipoSeguro.equalsIgnoreCase("Intermedio") || tipoSeguro.equalsIgnoreCase("Premium") && accidentes == true) {
            return (20 * preciobase) / 100 + preciobase;
        } else if (edad >= 25 && edad <= 40 && tipoSeguro.equalsIgnoreCase("Premium") && anyosSeguros >= 3 && edadCoche >= 1 && accidentes == false) {
            return (15 * preciobase) / 100 - preciobase;
        } else {
            return preciobase;
        }
    }
}
